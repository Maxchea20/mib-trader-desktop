"""

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
    # Paper policy from 30d/90d research: SHORT FIRE sleeve was a drag
    # (30d PF 0.82). Brain still computes SHORT. This gate only blocks
    # opening/flipping into SHORT paper trades. LIVE remains unused.
    "allowed_sides": "LONG",
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
    if "allowed_sides" in payload:
        sides = str(payload["allowed_sides"]).upper()
        if sides in ("LONG", "SHORT", "BOTH"):
            CONFIG["allowed_sides"] = sides
    if payload.get("timeframe"):
        CONFIG["timeframe"] = str(payload["timeframe"])
    for k in ("notional_usd", "sl_atr_mult", "tp_atr_mult"):
        if k in payload and payload[k] is not None:
            try:
                CONFIG[k] = float(payload[k])
            except (TypeError, ValueError):
                pass
    if turned_on:
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
    thesis = None
    try:
        brain_with_price = dict(brain)
        brain_with_price["price"] = price
        thesis = brain_position.build_thesis(brain_with_price)
    except Exception:
        pass
    paper_trading.open_trade(
        symbol=SYMBOL, side=side, entry_price=price, sl_price=sl, tp_price=tp,
        notional_usd=CONFIG["notional_usd"], timeframe=tf,
        brain_state=brain.get("state", ""), consensus=brain.get("consensus_score", 0),
        confidence=brain.get("confidence", 0), note="auto-trade", source="AUTO",
        decision_id=decision_id, thesis=thesis,
    )


def _in_cooldown(tf_seconds: int) -> bool:
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
    candles = dao.read_closed_candles(tf, limit=ANALYSIS_LOOKBACK)
    if len(candles) < 30:
        return STATE
    last_ts = candles[-1]["ts"]

    open_auto_before = _open_auto()
    if open_auto_before is None and STATE.get("_had_open_auto"):
        recent = paper_trading.list_trades("CLOSED")
        recent_auto = next((t for t in recent if t.get("source") == "AUTO"), None)
        if recent_auto and recent_auto.get("exit_reason") in ("SL", "TP"):
            _record_close(recent_auto["exit_reason"])
    STATE["_had_open_auto"] = open_auto_before is not None

    if not force and STATE["last_candle_ts"] == last_ts:
        return STATE
    STATE["last_candle_ts"] = last_ts

    result = analysis_service.full_analysis(tf)
    brain = result.get("brain") or {}
    state = brain.get("state")
    price = live_price or result.get("price")
    STATE["last_state"] = state
    STATE["last_eval_at"] = int(time.time())

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

    if CONFIG.get("mode") == "LIVE":
        STATE["last_action"] = "LIVE MODE - NO ORDER"
        STATE["last_reason"] = "Live execution is not enabled"
        return STATE

    open_auto = _open_auto()
    tf_seconds = _tf_seconds(tf)

    if open_auto is not None:
        _manage_open_position(open_auto, result, price, tf, decision_id)
        open_auto = _open_auto()

    if state in ("LONG", "SHORT"):
        allowed = CONFIG.get("allowed_sides", "BOTH")
        if allowed in ("LONG", "SHORT") and state != allowed:
            if open_auto is None:
                STATE["last_action"] = f"SKIP {state} (allowed_sides={allowed})"
                STATE["last_reason"] = (
                    f"Brain {state} but paper policy is {allowed}-only — not opening"
                )
                return STATE
            if open_auto["side"] != state:
                STATE["last_action"] = f"HOLD {open_auto['side']} (no flip to {state})"
                STATE["last_reason"] = (
                    f"Brain flipped to {state} but paper policy blocks that side"
                )
                return STATE
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
    return STATE


def _tf_seconds(tf: str) -> int:
    from .config import TF_SECONDS
    return TF_SECONDS.get(tf, 900)


def _manage_open_position(open_auto: Dict, result: Dict, price: float, tf: str,
                          decision_id: Optional[str]) -> None:
    thesis = paper_trading.get_thesis(open_auto["id"])
    if not thesis:
        return
    agents = result.get("agents") or []
    current_decision = result.get("brain") or {}
    try:
        rec = brain_position.evaluate_open_position(thesis, agents, current_decision)
    except Exception:
        return
    STATE["last_position_recommendation"] = rec

    try:
        from . import decision_log
        decision_log.logger.record_trade_event(
            trade_id=open_auto["id"], event_type=f"POSITION_MANAGEMENT_{rec['recommendation']}",
            details={"thesis_state": rec["thesis_state"], "consensus_delta": rec["consensus_delta"],
                    "reasons": rec["reasons"], "structural_failures": rec["structural_failures"]},
        )
    except Exception:
        pass

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
