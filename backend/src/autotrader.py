"""Hands-free auto-trader.

Evaluates the Brain (all 10 agents + HTF regime) on each new candle close of the
auto-trade timeframe and manages a single AUTO paper position:
  - Brain LONG/SHORT with no AUTO position open  -> open one
  - Brain flips to the opposite side             -> close current (FLIP) + open new
  - Brain same side                              -> hold
  - Brain WAIT/AVOID                             -> do nothing (let SL/TP manage)

SL/TP are derived from ATR of the auto timeframe. Simulated only.
"""
import time
from typing import Dict, Optional

from .config import SYMBOL, ANALYSIS_LOOKBACK
from .market_data import data_access as dao
from .indicators import arrays, atr
from . import analysis_service
from . import paper_trading
from .brain import position as brain_position

CONFIG = {
    "enabled": True,
    "mode": "PAPER",
    "timeframe": "15m",
    "notional_usd": 1000.0,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    # Post-trade re-evaluation (spec section 9) — a brief pause before a
    # FRESH Brain decision is allowed to open a new position, not a
    # blind permanent timer. Longer after a thesis failure (SL/BRAIN_EXIT)
    # than after a clean TP, since a stopped-out or invalidated thesis
    # deserves more caution than a target that was simply reached.
    "cooldown_bars_normal": 1,
    "cooldown_bars_after_failure": 3,
}

STATE = {
    "last_candle_ts": None,
    "last_state": None,
    "last_action": None,
    "last_reason": None,
    "last_eval_at": None,
    "last_close_ts": None,
    "last_close_reason": None,
    "last_position_recommendation": None,
}


def _open_auto() -> Optional[Dict]:
    for t in paper_trading.list_trades("OPEN"):
        if t.get("source") == "AUTO":
            return t
    return None


def status() -> Dict:
    return {"config": CONFIG, "state": STATE, "open_auto_trade": _open_auto()}


def update(payload: Dict) -> Dict:
    turned_on = False
    if "enabled" in payload:
        new_enabled = bool(payload["enabled"])
        turned_on = new_enabled and not CONFIG["enabled"]
        CONFIG["enabled"] = new_enabled
    if "mode" in payload:
        mode = str(payload["mode"]).upper()
        if mode in ("PAPER", "LIVE"):
            CONFIG["mode"] = mode
    if payload.get("timeframe"):
        CONFIG["timeframe"] = str(payload["timeframe"])
    for k in ("notional_usd", "sl_atr_mult", "tp_atr_mult"):
        if k in payload and payload[k] is not None:
            try:
                CONFIG[k] = float(payload[k])
            except (TypeError, ValueError):
                pass
    if turned_on:
        # act immediately when switched on
        try:
            from .market_data import manager
            evaluate(manager.STATE.get("last_price"), force=True)
        except Exception:
            pass
    return status()


def _open(side: str, price: float, tf: str, brain: Dict, candles, decision_id: Optional[str] = None) -> None:
    a = arrays(candles)
    _atr = atr(a["high"], a["low"], a["close"], 14)
    if _atr <= 0:
        _atr = price * 0.005
    if side == "LONG":
        sl = price - CONFIG["sl_atr_mult"] * _atr
        tp = price + CONFIG["tp_atr_mult"] * _atr
    else:
        sl = price + CONFIG["sl_atr_mult"] * _atr
        tp = price - CONFIG["tp_atr_mult"] * _atr
    # Immutable original thesis (spec section 2) — snapshot NOW, at the
    # moment of entry, never touched again after this. build_thesis()
    # reads straight from the same `brain` dict that decided to fire,
    # so this is exactly what Brain believed when it opened the trade.
    thesis = None
    try:
        brain_with_price = dict(brain)
        brain_with_price["price"] = price
        thesis = brain_position.build_thesis(brain_with_price)
    except Exception:
        pass  # thesis storage failing must never block opening the actual trade
    paper_trading.open_trade(
        symbol=SYMBOL, side=side, entry_price=price, sl_price=sl, tp_price=tp,
        notional_usd=CONFIG["notional_usd"], timeframe=tf,
        brain_state=brain.get("state", ""), consensus=brain.get("consensus_score", 0),
        confidence=brain.get("confidence", 0), note="auto-trade", source="AUTO",
        decision_id=decision_id, thesis=thesis,
    )


def _in_cooldown(tf_seconds: int) -> bool:
    """Post-trade re-evaluation pause (spec section 9) — NOT a blind
    permanent timer, just enough bars for a genuinely fresh Brain
    decision to form before re-entering. Longer after a thesis
    failure than after a clean TP."""
    if STATE["last_close_ts"] is None:
        return False
    bars = CONFIG["cooldown_bars_after_failure"] if STATE["last_close_reason"] in ("SL", "BRAIN_EXIT") \
        else CONFIG["cooldown_bars_normal"]
    elapsed = time.time() - STATE["last_close_ts"]
    return elapsed < bars * tf_seconds


def _record_close(reason: str) -> None:
    STATE["last_close_ts"] = time.time()
    STATE["last_close_reason"] = reason


def evaluate(live_price: Optional[float], force: bool = False) -> Dict:
    if not CONFIG["enabled"]:
        STATE["last_action"] = "DISABLED"
        return STATE
    tf = CONFIG["timeframe"]
    from .market_data.closed_candles import filter_closed
    candles = filter_closed(dao.read_candles(tf, limit=ANALYSIS_LOOKBACK + 2), tf)
    if len(candles) < 30:
        return STATE
    last_ts = candles[-1]["ts"]

    open_auto_before = _open_auto()  # checked BEFORE the new-candle
    # gate below, so an externally-triggered close (SL/TP, happening in
    # between evaluate() calls via a separate live-price path) is still
    # detected and cooled-down correctly even if this exact call
    # otherwise short-circuits on the "no new candle" check.
    if open_auto_before is None and STATE.get("_had_open_auto"):
        # The AUTO position that was open last time is gone now, and
        # THIS function didn't close it (that always clears the flag
        # below) — so something else did: SL or TP.
        recent = paper_trading.list_trades("CLOSED")
        recent_auto = next((t for t in recent if t.get("source") == "AUTO"), None)
        if recent_auto and recent_auto.get("exit_reason") in ("SL", "TP"):
            _record_close(recent_auto["exit_reason"])
    STATE["_had_open_auto"] = open_auto_before is not None

    if not force and STATE["last_candle_ts"] == last_ts:
        return STATE  # only act on a new candle
    STATE["last_candle_ts"] = last_ts

    result = analysis_service.full_analysis(tf)
    brain = result.get("brain") or {}
    state = brain.get("state")
    price = live_price or result.get("price")
    STATE["last_state"] = state
    STATE["last_eval_at"] = int(time.time())

    # --- Decision logging: purely observational, cannot affect anything
    # below this point. Wrapped here even though observe() already
    # catches its own exceptions internally — defense in depth per the
    # spec's "logging must never block the trading engine" requirement.
    decision_id = None
    try:
        from .market_data import manager as _mgr
        ticker = _mgr.live_status().get("ticker") or {}
        from . import decision_log
        decision_id = decision_log.logger.observe(
            result, CONFIG["sl_atr_mult"], CONFIG["tp_atr_mult"], CONFIG["notional_usd"],
            bid=ticker.get("bid"), ask=ticker.get("ask"),
        )
    except Exception:
        pass
    # --- end decision logging ---

    if CONFIG.get("mode") == "LIVE":
        STATE["last_action"] = "LIVE MODE - NO ORDER"
        STATE["last_reason"] = "Live execution is not enabled"
        return STATE

    open_auto = _open_auto()
    tf_seconds = _tf_seconds(tf)

    if open_auto is not None:
        # POSITION MANAGEMENT MODE (spec section 33) — a position is
        # already open, so Brain re-evaluates the ORIGINAL thesis
        # against current evidence rather than making a fresh entry
        # decision. This runs regardless of whether the fresh `state`
        # above is LONG/SHORT/WAIT/AVOID — a thesis can weaken even
        # while the raw consensus hasn't yet flipped sides.
        _manage_open_position(open_auto, result, price, tf, decision_id)
        # After managing (which may have closed the position via
        # BRAIN_EXIT above), re-check before considering a flip/hold.
        open_auto = _open_auto()

    if state in ("LONG", "SHORT"):
        if open_auto is None:
            if _in_cooldown(tf_seconds):
                STATE["last_action"] = "COOLDOWN"
                STATE["last_reason"] = (
                    f"Post-trade re-evaluation pause after {STATE['last_close_reason']} "
                    "— waiting for a fresh setup before re-entering"
                )
            else:
                _open(state, price, tf, brain, candles, decision_id)
                STATE["last_action"] = f"OPEN {state}"
                STATE["last_reason"] = f"Brain {state} @ consensus {brain.get('consensus_score')}"
        elif open_auto["side"] != state:
            paper_trading.close_trade(open_auto["id"], price, "FLIP")
            _record_close("FLIP")
            if _in_cooldown(tf_seconds):
                STATE["last_action"] = f"FLIP-CLOSE (cooldown before {state})"
                STATE["last_reason"] = "Closed on opposite signal; cooling down before re-entry"
            else:
                _open(state, price, tf, brain, candles, decision_id)
                STATE["last_action"] = f"FLIP -> {state}"
                STATE["last_reason"] = f"Brain flipped to {state}"
        else:
            STATE["last_action"] = f"HOLD {state}"
            STATE["last_reason"] = "Signal unchanged"
    else:
        if open_auto is None:
            STATE["last_action"] = f"NO-TRADE ({state})"
            STATE["last_reason"] = f"Brain {state}"
        # else: position management above already handled this cycle —
        # WAIT/AVOID on fresh consensus doesn't override an existing,
        # still-valid thesis.
    return STATE


def _tf_seconds(tf: str) -> int:
    from .config import TF_SECONDS
    return TF_SECONDS.get(tf, 900)


def _manage_open_position(open_auto: Dict, result: Dict, price: float, tf: str,
                          decision_id: Optional[str]) -> None:
    """Brain V2 position management (spec sections 33-37). Brain only
    RECOMMENDS — this function is the one and only place that turns a
    recommendation into an action, and the only action it ever takes
    is closing via the existing paper_trading.close_trade() (the same
    mechanism SL/TP/FLIP already use). PROTECT/REDUCE are logged as
    recommendations but not acted on, since no partial-close mechanism
    exists in this codebase — inventing one wasn't part of this task
    and would be new infrastructure, not wiring.
    """
    thesis = paper_trading.get_thesis(open_auto["id"])
    if not thesis:
        return  # trade opened without a thesis (e.g. MANUAL) — nothing to manage against
    agents = result.get("agents") or []
    current_decision = result.get("brain") or {}
    try:
        rec = brain_position.evaluate_open_position(thesis, agents, current_decision)
    except Exception:
        return  # position management must never crash the trading loop
    STATE["last_position_recommendation"] = rec

    try:
        from . import decision_log
        decision_log.logger.record_trade_event(
            trade_id=open_auto["id"], event_type=f"POSITION_MANAGEMENT_{rec['recommendation']}",
            details={"thesis_state": rec["thesis_state"], "consensus_delta": rec["consensus_delta"],
                    "reasons": rec["reasons"], "structural_failures": rec["structural_failures"]},
        )
    except Exception:
        pass  # observational logging must never affect the recommendation/action below

    if rec["recommendation"] == "EXIT":
        paper_trading.close_trade(open_auto["id"], price, "BRAIN_EXIT")
        _record_close("BRAIN_EXIT")
        STATE["last_action"] = "BRAIN_EXIT"
        STATE["last_reason"] = "; ".join(rec["reasons"])
    elif rec["recommendation"] in ("PROTECT", "REDUCE"):
        STATE["last_action"] = f"RECOMMEND {rec['recommendation']}"
        STATE["last_reason"] = "; ".join(rec["reasons"])
    else:
        STATE["last_action"] = f"HOLD (thesis {rec['thesis_state'].lower()})"
        STATE["last_reason"] = "; ".join(rec["reasons"])
