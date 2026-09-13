"""Walk-forward backtest — hunt + V1b lifecycle + weather (default).

Old agent-vote path remains if use_hunt_lifecycle=False.
"""
from __future__ import annotations

import threading
import uuid
from typing import Dict, List, Optional

from .config import SYMBOL, HTF_TIMEFRAMES, ANALYSIS_LOOKBACK, TIMEFRAMES, TF_SECONDS
from .market_data import data_access as dao
from .market_state.builder import build_market_state
from .market_state.actionable import apply_actionable_structure
from .market_data.closed_candles import filter_closed
from . import analysis_service as svc
from .analysis_service import _native
from .brain import brain as brain_engine
from .brain.mtf import regime_from_agents
from .brain.brain import _name as engine_display_name
from .indicators import arrays, atr
from .backtest_attribution import attribution, summary_stats

LONG = "LONG"
SHORT = "SHORT"


def _htf_regime_at(ts: int, htf_candles: Dict[str, List], cache: Dict,
                    weights_override: Optional[Dict] = None,
                    htf_gate_override: Optional[Dict] = None,
                    pivot_window_override: Optional[int] = None) -> Dict:
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


def _pref(rows, ts):
    return [c for c in rows if c["ts"] <= ts]


class WalkForwardBacktest:
    def __init__(self, timeframe: str = "15m", days: float = 7.0,
                 start_balance: float = 250.0, capital_pct: float = 10.0,
                 leverage: float = 5.0, sl_atr_mult: float = 1.5,
                 tp_atr_mult: float = 2.5, weights_override: Optional[Dict[str, float]] = None,
                 htf_timeframes: Optional[List[str]] = None, use_htf_gate: bool = True,
                 entry_override: Optional[Dict[str, float]] = None,
                 conflict_override: Optional[Dict[str, float]] = None,
                 htf_gate_override: Optional[Dict[str, float]] = None,
                 pivot_window_override: Optional[int] = None,
                 progress_callback: Optional[callable] = None,
                 use_weather: bool = True,
                 use_hunt_lifecycle: bool = True):
        self.timeframe = timeframe
        self.days = days
        self.start_balance = start_balance
        self.capital_pct = capital_pct
        self.leverage = leverage
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.weights_override = weights_override
        self.entry_override = entry_override
        self.conflict_override = conflict_override
        self.htf_gate_override = htf_gate_override
        self.pivot_window_override = pivot_window_override
        valid = [t for t in (htf_timeframes or []) if t in TIMEFRAMES]
        self.htf_timeframes = valid if valid else list(HTF_TIMEFRAMES)
        self.use_htf_gate = use_htf_gate
        self.use_weather = use_weather
        self.use_hunt_lifecycle = use_hunt_lifecycle
        self.id = f"BT-{uuid.uuid4().hex[:10]}"
        self.status = "pending"
        self.progress = 0.0
        self.error: Optional[str] = None
        self.result: Optional[Dict] = None
        self._stop = False
        self.progress_callback = progress_callback

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

        candles = filter_closed(dao.read_candles(tf, limit=need), tf)
        m5_all = filter_closed(dao.read_candles("5m", limit=need * 4), "5m") if tf == "15m" else []
        c4_all = filter_closed(dao.read_candles("4h", limit=4000), "4h")
        c1_all = filter_closed(dao.read_candles("1h", limit=10000), "1h")
        if len(candles) < ANALYSIS_LOOKBACK + 30:
            self.result = {"error": "insufficient_data", "timeframe": tf,
                            "have": len(candles), "need": need}
            self.status = "error"
            return

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

        for i in range(start, n):
            if self._stop:
                break
            bar = candles[i]
            hi, lo, close_price = float(bar["high"]), float(bar["low"]), float(bar["close"])
            ts = bar["ts"]

            if open_pos is not None:
                hit = None
                if open_pos["side"] == LONG:
                    if lo <= open_pos["sl"]:
                        hit = ("SL", open_pos["sl"])
                    elif hi >= open_pos["tp"]:
                        hit = ("TP", open_pos["tp"])
                else:
                    if hi >= open_pos["sl"]:
                        hit = ("SL", open_pos["sl"])
                    elif lo <= open_pos["tp"]:
                        hit = ("TP", open_pos["tp"])
                if hit:
                    reason, exit_price = hit
                    balance = self._close(open_pos, exit_price, reason, ts, balance, trades)
                    open_pos = None

            window = candles[max(0, i - ANALYSIS_LOOKBACK):i + 1]
            if len(window) < 30:
                equity_curve.append({"ts": ts, "equity": round(balance, 2)})
                continue
            m5_window = None
            if tf == "15m" and m5_all:
                horizon = int(ts) + TF_SECONDS[tf]
                m5_window = [
                    c for c in m5_all
                    if int(c["ts"]) + 300 <= horizon and int(c["ts"]) >= int(window[0]["ts"])
                ]

            if self.use_hunt_lifecycle:
                from .brain.observation_hunt import evaluate_hunt, _fresh
                from .brain.lifecycle import position_from_fire, reevaluate, EXIT, TRAIL
                from .brain.weather import classify, side_allowed
                from .structure.observe import observe as obs_structure
                from .trend.observe import observe as obs_trend

                w4 = _pref(c4_all, ts)[-200:]
                w1h = _pref(c1_all, ts)[-200:]
                wx = classify(w4, w1h) if self.use_weather else {"flag": "CHOP"}
                st_ev = st_dir = None
                if len(window) >= 60:
                    st = obs_structure(window, tf)
                    for e in _fresh(st, ts):
                        if e.event_type in ("CHoCH", "CHOCH"):
                            st_ev, st_dir = e.event_type, e.direction
                            break
                h1_state = obs_trend(w1h, "1h").state if len(w1h) >= 60 else None

                if open_pos is not None and open_pos.get("_lc") is not None:
                    level_lost = (
                        (open_pos["side"] == LONG and close_price < open_pos["entry_price"])
                        or (open_pos["side"] == SHORT and close_price > open_pos["entry_price"])
                    )
                    m5_here = [c for c in (m5_window or []) if int(c["ts"]) >= ts and int(c["ts"]) < ts + 900]
                    if not m5_here:
                        m5_here = [bar]
                    for b5 in m5_here:
                        rec = reevaluate(
                            open_pos["_lc"],
                            price=float(b5["close"]),
                            high=float(b5["high"]),
                            low=float(b5["low"]),
                            structure_event=st_ev,
                            structure_dir=st_dir,
                            trend_1h_state=h1_state,
                            level_lost=level_lost and int(b5["ts"]) >= ts + 840,
                            now_ts=b5.get("ts"),
                        )
                        if rec.get("action") == TRAIL and rec.get("sl") is not None:
                            open_pos["sl"] = rec["sl"]
                            try:
                                open_pos["_lc"].sl = rec["sl"]
                            except Exception:
                                pass
                        elif rec.get("action") == EXIT:
                            px = rec.get("exit_px") or float(b5["close"])
                            kind = rec.get("exit_kind") or rec.get("reason") or "BRAIN_EXIT"
                            balance = self._close(open_pos, px, kind, ts, balance, trades)
                            open_pos = None
                            break

                if open_pos is None:
                    fill = bar
                    if m5_window:
                        near = [c for c in m5_window if int(c["ts"]) >= ts + 800]
                        if near:
                            fill = near[0]
                    fire = evaluate_hunt(window, fill, candles_4h=w4)
                    if fire.get("action") == "FIRE":
                        side = fire.get("direction") or fire.get("side")
                        ok = (not self.use_weather) or side_allowed(wx.get("flag"), side)
                        if ok and side in (LONG, SHORT):
                            atr15 = float(fire.get("atr_15m") or 0) or abs(
                                float(fire["entry"]) - float(fire["stop"])
                            )
                            entry = float(fire["entry"])
                            if side == LONG:
                                fire["stop"] = entry - self.sl_atr_mult * atr15
                                fire["target"] = entry + self.tp_atr_mult * atr15
                            else:
                                fire["stop"] = entry + self.sl_atr_mult * atr15
                                fire["target"] = entry - self.tp_atr_mult * atr15
                            fire["atr_15m"] = atr15
                            dummy = {"state": side, "consensus_score": 0, "confidence": 0}
                            open_pos = self._open(side, entry, ts, [], dummy, window, balance)
                            open_pos["sl"] = fire["stop"]
                            open_pos["tp"] = fire["target"]
                            open_pos["_lc"] = position_from_fire(
                                fire,
                                trade_id=str(ts),
                                equity=balance,
                                risk_pct=max(0.005, (self.capital_pct / 100.0) * 0.2),
                                opened_ts=ts,
                            )
            else:
                market_state = build_market_state(
                    window, symbol=SYMBOL, timeframe=tf,
                    pivot_window_override=self.pivot_window_override,
                )
                apply_actionable_structure(
                    market_state.structure, window, market_state.volatility.atr,
                    m5_candles=m5_window, timeframe=tf,
                )
                agents = svc.run_agents(
                    window, tf, market_state=market_state,
                    pivot_window_override=self.pivot_window_override,
                )
                if self.use_htf_gate:
                    htf = _htf_regime_at(
                        ts, htf_candles, cache, self.weights_override,
                        self.htf_gate_override, self.pivot_window_override,
                    )
                else:
                    htf = {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
                _w = arrays(window)
                bar_atr = atr(_w["high"], _w["low"], _w["close"], 14) if len(window) >= 15 else 0.0
                decision = brain_engine.decide(
                    agents, close_price, tf, htf, self.weights_override,
                    self.entry_override, self.conflict_override,
                    self.htf_gate_override, atr_value=bar_atr,
                )
                state = decision["state"]
                if state in (LONG, SHORT):
                    if open_pos is None:
                        open_pos = self._open(state, close_price, ts, agents, decision, window, balance)
                    elif open_pos["side"] != state:
                        balance = self._close(open_pos, close_price, "FLIP", ts, balance, trades)
                        open_pos = self._open(state, close_price, ts, agents, decision, window, balance)

            equity_curve.append({"ts": ts, "equity": round(balance, 2)})
            self.progress = (i - start + 1) / max(1, n - start)
            if self.progress_callback:
                self.progress_callback(self)

        if open_pos is not None:
            last_close = float(candles[-1]["close"])
            balance = self._close(open_pos, last_close, "END_OF_DATA", candles[-1]["ts"], balance, trades)

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
                "pivot_window_forced": self.pivot_window_override,
                "htf_timeframes": self.htf_timeframes,
                "use_htf_gate": self.use_htf_gate,
                "use_weather": self.use_weather,
                "use_hunt_lifecycle": self.use_hunt_lifecycle,
            },
            "actual_days_tested": round(actual_days, 1),
            "data_shortfall": actual_days < self.days * 0.9,
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

        agent_votes = {}
        strongest = None
        try:
            agent_votes = {
                ar.agent: {"direction": ar.direction, "confidence": round(ar.confidence, 1)}
                for ar in (agents or [])
            }
            aligned = [ar for ar in (agents or []) if getattr(ar, "valid", False) and ar.direction == side]
            strongest = max(aligned, key=lambda ar: ar.confidence).agent if aligned else None
        except Exception:
            pass

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
            "agent_votes": pos.get("agent_votes") or {}, "strongest_engine": pos.get("strongest_engine"),
            "consensus_score": pos.get("consensus_score"), "confidence": pos.get("confidence"),
            "status": "closed",
        })
        return balance + pnl


class BacktestRunner:
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