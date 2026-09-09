"""Walk-forward backtest — real bar-by-bar trade simulation.

Built after reviewing mib-gold's backtest harness, which has one design
principle worth copying exactly: "Same engines, consensus, risk, trailing
and book as live." It doesn't reimplement a simplified version of the
strategy for testing — it feeds historical bars through the *actual*
production decision path. This module does the same thing here, calling
analysis_service.run_agents() and brain.decide() — the exact functions
autotrader.py uses live — rather than any separate approximation.

This replaces the old backtest.py's "check price N candles later" method
with real trade lifecycle simulation: a position opens, then every
subsequent bar's high/low is checked against its actual SL/TP until one
is hit (or the Brain flips, or data runs out). That's a materially more
honest test of the strategy than a fixed forward-return window, at the
cost of being slower to run (it's why this runs as a background job with
progress polling, same as mib-gold does).

Known simplifications vs mib-gold, called out explicitly rather than
silently glossed over:
  - Single position at a time, no layering/pyramiding (mib-gold's
    PositionBook supports up to N stacked layers; this does not).
  - No spread or slippage modeled (mib-gold explicitly simulates both).
  - No news gate (mib-gold can block entries around calendar events).
  - Worst-case same-bar SL/TP ordering: if a single bar's range touches
    both the stop and the target (only knowable with OHLC, not exact
    intrabar path), this always resolves it as the STOP hitting first.
    That's deliberately the pessimistic assumption, matching mib-gold's
    own stated "stops filled worst-case" philosophy — never let a
    backtest flatter itself by assuming the lucky order of events.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, List, Optional

from .config import SYMBOL, HTF_TIMEFRAMES, ANALYSIS_LOOKBACK, TIMEFRAMES, TF_SECONDS
from .market_data import data_access as dao
from . import analysis_service as svc
from .analysis_service import _native
from .brain import brain as brain_engine
from .brain.mtf import regime_from_agents
from .brain.brain import _name as engine_display_name
from .indicators import arrays, atr
from .backtest_attribution import attribution, summary_stats

LONG = "LONG"
SHORT = "SHORT"

# Backtest runs bar-by-bar in a background thread, sharing the GIL with
# the async event loop that serves /status polls. Under load (long
# backtests, esp. on Windows where OS thread-scheduling granularity is
# coarser than Linux) that can measurably delay how quickly a pending
# status request actually gets serviced — see the diagnostic report for
# direct, measured evidence (worst-case poll latency scaling with
# backtest length: 160ms on a 10-day run, 484ms on 40-day).
#
# GIL_YIELD_INTERVAL_BARS controls how often this loop explicitly yields
# (time.sleep(0), a genuine no-op in wall-clock terms — it does not
# actually pause execution, it just gives the OS scheduler a deterministic
# opportunity to switch threads right here) rather than relying solely on
# CPython's automatic time-based GIL switching. Every bar was measured to
# add negligible overhead (~3%), but yielding this often is unnecessary —
# a periodic yield every few bars gives the event loop frequent enough
# opportunities to respond promptly without adding avoidable scheduling
# overhead on every single iteration.
GIL_YIELD_INTERVAL_BARS = 5


def _htf_regime_at(ts: int, htf_candles: Dict[str, List], cache: Dict,
                    weights_override: Optional[Dict] = None,
                    htf_gate_override: Optional[Dict] = None,
                    pivot_window_override: Optional[int] = None) -> Dict:
    """Identical approach to the existing backtest.py: slice HTF candles to
    only those at-or-before the simulated bar's timestamp, so the regime
    gate never sees the future. This is the load-bearing anti-lookahead
    guard — if this ever used the full current HTF history instead, every
    result from this module would be silently cheating."""
    agents_by_tf = {}
    subs = {}
    for tf, candles in htf_candles.items():
        sub = [c for c in candles if c["ts"] <= ts]
        subs[tf] = len(sub)
        if len(sub) >= 60:
            agents_by_tf[tf] = sub[-ANALYSIS_LOOKBACK:]
    key = tuple(sorted(subs.items()))
    if key in cache:
        return cache[key]
    if not agents_by_tf:
        res = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
    else:
        by_tf = {tf: svc.run_agents(c, tf, pivot_window_override=pivot_window_override)
                 for tf, c in agents_by_tf.items()}
        res = regime_from_agents(by_tf, weights_override, htf_gate_override)
    cache[key] = res
    return res


class WalkForwardBacktest:
    def __init__(self, timeframe: str = "15m", days: float = 7.0,
                 start_balance: float = 250.0, capital_pct: float = 10.0,
                 leverage: float = 5.0, sl_atr_mult: float = 1.5,
                 tp_atr_mult: float = 2.5, weights_override: Optional[Dict[str, float]] = None,
                 htf_timeframes: Optional[List[str]] = None, use_htf_gate: bool = True,
                 entry_override: Optional[Dict[str, float]] = None,
                 conflict_override: Optional[Dict[str, float]] = None,
                 htf_gate_override: Optional[Dict[str, float]] = None,
                 pivot_window_override: Optional[int] = None):
        self.timeframe = timeframe
        self.days = days
        self.start_balance = start_balance
        self.capital_pct = capital_pct
        self.leverage = leverage
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.weights_override = weights_override
        # Same pattern as weights_override: None means "read live settings
        # fresh at decision time", an explicit dict means "test this
        # hypothetical instead, without ever touching live settings." All
        # four settings panels (weights, entry, conflict, htf_gate) now
        # support this, matching what the Settings page itself exposes —
        # so nothing in Settings can be untestable here.
        self.entry_override = entry_override
        self.conflict_override = conflict_override
        self.htf_gate_override = htf_gate_override
        # None = use the new dynamic per-timeframe pivot window (the live
        # default). Pass an int (e.g. 5) to force the OLD fixed window
        # instead, for an A/B comparison of dynamic vs fixed swing
        # detection — this is NOT one of the four Settings-page panels,
        # it's a structural-detection parameter, but it gets the same
        # "test safely, never touches live" treatment.
        self.pivot_window_override = pivot_window_override

        # Independent from the live HTF_TIMEFRAMES config on purpose — this
        # lets a backtest test "what if the regime gate looked at 1h/30m
        # instead of/alongside 4h/1d" without touching config.py or any
        # live behavior. Falls back to the live default set when not given,
        # so existing behavior (and any run that doesn't care) is unchanged.
        valid = [t for t in (htf_timeframes or []) if t in TIMEFRAMES]
        self.htf_timeframes = valid if valid else list(HTF_TIMEFRAMES)
        # False disables the regime gate entirely for this run — every bar
        # is treated as regime=NEUTRAL, which apply_gate() already treats
        # as "no extra score/confidence requirement, no bonus either" (see
        # mtf.py's apply_gate: regime==NEUTRAL -> extra_score=extra_conf=0).
        # That's the correct, minimal way to turn the gate off without
        # touching apply_gate's own logic.
        self.use_htf_gate = use_htf_gate

        self.id = f"BT-{uuid.uuid4().hex[:10]}"
        self.status = "pending"
        self.progress = 0.0
        self.error: Optional[str] = None
        self.result: Optional[Dict] = None
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self) -> Dict:
        self.status = "running"
        try:
            self._run()
            self.status = "stopped" if self._stop else "done"
        except Exception as e:
            self.status, self.error = "error", f"{type(e).__name__}: {e}"
            raise
        return self.result

    def _run(self):
        tf = self.timeframe
        bar_minutes = TF_SECONDS[tf] / 60
        bars_wanted = int(self.days * 24 * 60 / bar_minutes)
        need = ANALYSIS_LOOKBACK + bars_wanted + 5

        candles = dao.read_candles(tf, limit=need)
        if len(candles) < ANALYSIS_LOOKBACK + 30:
            self.result = {"error": "insufficient_data", "timeframe": tf,
                            "have": len(candles), "need": need}
            self.status = "error"
            return

        # Only load HTF candle history if the gate is actually going to be
        # used this run — skipping it when disabled also makes gate-off
        # runs meaningfully faster, not just behaviorally different.
        htf_candles = ({t: dao.read_candles(t, limit=ANALYSIS_LOOKBACK * 3) for t in self.htf_timeframes}
                       if self.use_htf_gate else {})
        cache: Dict = {}

        n = len(candles)
        start = max(ANALYSIS_LOOKBACK, n - bars_wanted)
        actual_days = (candles[-1]["ts"] - candles[start]["ts"]) / 86400 if n > start else 0.0

        balance = self.start_balance
        equity_curve = []
        trades: List[Dict] = []
        open_pos: Optional[Dict] = None
        bars_log = []

        for i in range(start, n):
            if self._stop:
                break
            bar = candles[i]
            hi, lo, close_price = float(bar["high"]), float(bar["low"]), float(bar["close"])
            ts = bar["ts"]

            # --- 1. Check the CURRENTLY open position against this bar first,
            #        exactly like a live system finding out what happened on
            #        the newest candle before deciding anything else. ---
            if open_pos is not None:
                hit = None
                if open_pos["side"] == LONG:
                    if lo <= open_pos["sl"]:
                        hit = ("SL", open_pos["sl"])
                    elif hi >= open_pos["tp"]:
                        hit = ("TP", open_pos["tp"])
                else:  # SHORT
                    if hi >= open_pos["sl"]:
                        hit = ("SL", open_pos["sl"])
                    elif lo <= open_pos["tp"]:
                        hit = ("TP", open_pos["tp"])
                if hit:
                    reason, exit_price = hit
                    balance = self._close(open_pos, exit_price, reason, ts, balance, trades)
                    open_pos = None

            # --- 2. Build the analysis window and get a fresh decision,
            #        using the exact same functions live trading calls. ---
            window = candles[max(0, i - ANALYSIS_LOOKBACK):i + 1]
            if len(window) < 30:
                equity_curve.append({"ts": ts, "equity": round(balance, 2)})
                continue
            agents = svc.run_agents(window, tf, pivot_window_override=self.pivot_window_override)
            # Periodic GIL-yield — see GIL_YIELD_INTERVAL_BARS above for
            # the full rationale. This is the actual root-cause mitigation
            # traced to measured evidence (see diagnostic report) — not a
            # frontend polling change, which was already fixed separately
            # and was never the underlying problem.
            if (i - start) % GIL_YIELD_INTERVAL_BARS == 0:
                time.sleep(0)
            if self.use_htf_gate:
                htf = _htf_regime_at(ts, htf_candles, cache, self.weights_override, self.htf_gate_override, self.pivot_window_override)
            else:
                htf = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
            decision = brain_engine.decide(agents, close_price, tf, htf, self.weights_override,
                                           self.entry_override, self.conflict_override,
                                           self.htf_gate_override)
            state = decision["state"]

            # --- 3. Same open/flip/hold logic as autotrader.py's live loop. ---
            if state in (LONG, SHORT):
                if open_pos is None:
                    open_pos = self._open(state, close_price, ts, agents, decision, window, balance)
                elif open_pos["side"] != state:
                    balance = self._close(open_pos, close_price, "FLIP", ts, balance, trades)
                    open_pos = self._open(state, close_price, ts, agents, decision, window, balance)
                # else: same side, hold — nothing to do

            equity_curve.append({"ts": ts, "equity": round(balance, 2)})
            self.progress = (i - start + 1) / max(1, n - start)

        # Flatten anything still open at the end of available data.
        if open_pos is not None:
            last_close = float(candles[-1]["close"])
            balance = self._close(open_pos, last_close, "END_OF_DATA", candles[-1]["ts"], balance, trades)

        # Reflect whatever settings this specific run actually used — either
        # the custom values being tested, or (if none given) the live
        # settings at the moment the run finished. Never mutates live
        # settings for any of the four panels.
        from . import settings as runtime_settings
        weights_used = self.weights_override if self.weights_override is not None else runtime_settings.weights()
        entry_used = self.entry_override if self.entry_override is not None else runtime_settings.entry()
        conflict_used = self.conflict_override if self.conflict_override is not None else runtime_settings.conflict()
        htf_gate_used = self.htf_gate_override if self.htf_gate_override is not None else runtime_settings.htf_gate()
        self.result = _native({
            "id": self.id,
            "status": "done",
            "timeframe": tf,
            "range": {
                "start_ts": candles[start]["ts"], "end_ts": candles[-1]["ts"],
                "bars": n - start,
            },
            "config": {
                "days": self.days, "start_balance": self.start_balance,
                "capital_pct": self.capital_pct,
                "leverage": self.leverage, "sl_atr_mult": self.sl_atr_mult,
                "tp_atr_mult": self.tp_atr_mult,
                "used_custom_weights": self.weights_override is not None,
                "used_custom_entry": self.entry_override is not None,
                "used_custom_conflict": self.conflict_override is not None,
                "used_custom_htf_gate_values": self.htf_gate_override is not None,
                "pivot_window_forced": self.pivot_window_override,  # None = used the new dynamic default
                "htf_timeframes": self.htf_timeframes,
                "use_htf_gate": self.use_htf_gate,
            },
            # Honest disclosure when the database simply doesn't have as
            # much history as was asked for — the old behavior silently
            # ran on whatever was available with zero indication, which
            # made a 60-day request quietly become a 10-day test with no
            # warning anywhere. Never again: this is always present, and
            # the frontend should treat any meaningful gap here as a loud
            # warning, not a footnote.
            "actual_days_tested": round(actual_days, 1),
            "data_shortfall": actual_days < self.days * 0.9,  # >10% short
            "weights_used": weights_used,
            "entry_used": entry_used,
            "conflict_used": conflict_used,
            "htf_gate_used": htf_gate_used,
            "stats": summary_stats(trades, self.start_balance),
            "attribution": attribution(trades, weights_used, svc.AGENT_ORDER, engine_display_name),
            "trades": trades,
            "equity_curve": equity_curve[::max(1, len(equity_curve) // 1500)],
            "final_balance": round(balance, 2),
        })

    def _open(self, side: str, price: float, ts: int, agents, decision, candles, balance: float) -> Dict:
        a = arrays(candles)
        _atr = atr(a["high"], a["low"], a["close"], 14)
        if _atr <= 0:
            _atr = price * 0.005
        if side == LONG:
            sl = price - self.sl_atr_mult * _atr
            tp = price + self.tp_atr_mult * _atr
        else:
            sl = price + self.sl_atr_mult * _atr
            tp = price - self.tp_atr_mult * _atr

        margin = balance * (self.capital_pct / 100.0)
        notional = margin * self.leverage

        agent_votes = {ar.agent: {"direction": ar.direction, "confidence": round(ar.confidence, 1)}
                       for ar in agents}
        aligned = [ar for ar in agents if ar.valid and ar.direction == side]
        strongest = max(aligned, key=lambda ar: ar.confidence).agent if aligned else None

        return {
            "id": str(uuid.uuid4()), "side": side, "entry_price": price, "sl": sl, "tp": tp,
            "notional_usd": notional, "opened_at": ts,
            "consensus_score": decision.get("consensus_score"), "confidence": decision.get("confidence"),
            "agent_votes": agent_votes, "strongest_engine": strongest,
        }

    def _close(self, pos: Dict, exit_price: float, reason: str, ts: int, balance: float,
               trades: List[Dict]) -> float:
        entry = pos["entry_price"]
        if pos["side"] == LONG:
            pnl = (exit_price - entry) / entry * pos["notional_usd"]
        else:
            pnl = (entry - exit_price) / entry * pos["notional_usd"]
        risk_usd = abs(entry - pos["sl"]) / entry * pos["notional_usd"]
        r_multiple = (pnl / risk_usd) if risk_usd > 0 else 0.0

        trades.append({
            "id": pos["id"], "direction": pos["side"], "entry_price": round(entry, 2),
            "exit_price": round(exit_price, 2), "sl": round(pos["sl"], 2), "tp": round(pos["tp"], 2),
            "notional_usd": round(pos["notional_usd"], 2), "opened_at": pos["opened_at"], "closed_at": ts,
            "exit_reason": reason, "outcome": "win" if pnl > 0 else "loss",
            "pnl": round(pnl, 2), "r_multiple": round(r_multiple, 3),
            "agent_votes": pos["agent_votes"], "strongest_engine": pos["strongest_engine"],
            "consensus_score": pos["consensus_score"], "confidence": pos["confidence"],
            "status": "closed",
        })
        return balance + pnl


class BacktestRunner:
    """Runs walk-forward backtests in a background thread so a slow
    multi-day simulation doesn't block the request. Keeps the last 3
    runs in memory for polling — same approach mib-gold uses."""

    def __init__(self):
        self.runs: Dict[str, WalkForwardBacktest] = {}
        self.order: List[str] = []

    def start(self, bt: WalkForwardBacktest) -> str:
        self.runs[bt.id] = bt
        self.order.append(bt.id)
        for old in self.order[:-3]:
            self.runs.pop(old, None)
        self.order = self.order[-3:]
        threading.Thread(target=bt.run, daemon=True, name=f"wfbt-{bt.id}").start()
        return bt.id

    def get(self, run_id: str) -> Optional[WalkForwardBacktest]:
        return self.runs.get(run_id)


runner = BacktestRunner()