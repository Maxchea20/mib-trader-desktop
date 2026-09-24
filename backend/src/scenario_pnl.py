"""Scenario SL/TP preview for the live-controls boxes.

Must not invent levels from a naked 15m close. That made the boxes
light up whenever Hunt/AI shouted FIRE even though Scenario was WAIT.
"""
from typing import Dict, Optional, Tuple

from .autotrader_state import CONFIG, STATE

_LIVE_ACTIONS = {"FIRE", "C_WATCHING", "WATCH", "SETUP", "ARMED"}


def scenario_levels() -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str], Optional[float]]:
    sc = STATE.get("last_scenario_result") or {}
    log = STATE.get("last_scenario_trade_log") or {}
    action = str(sc.get("action") or "").upper()
    armed = bool(sc.get("thesis_id") or log.get("thesis_id"))
    if action not in _LIVE_ACTIONS and not armed:
        return None, None, None, None, None
    entry = sc.get("entry") or log.get("actual_entry_price") or log.get("c_intended_price") or sc.get("origin_level")
    side = sc.get("direction") or log.get("direction")
    atr = sc.get("atr15") or log.get("atr15")
    sl_m = float(CONFIG.get("sl_atr_mult") or 1.5)
    tp_m = float(CONFIG.get("tp_atr_mult") or 2.5)
    sl = tp = None
    if entry and atr and atr > 0:
        e = float(entry)
        if str(side or "").upper() == "SHORT":
            sl, tp = e + sl_m * atr, e - tp_m * atr
        else:
            sl, tp = e - sl_m * atr, e + tp_m * atr
    return (float(entry) if entry else None), sl, tp, side, atr


def scenario_pnl_preview(sizing: Dict) -> Dict:
    entry, sl, tp, side, _atr = scenario_levels()
    out = {"sl_price": sl, "tp_price": tp, "entry_price": entry, "direction": side,
           "sl_pnl_usd": None, "tp_pnl_usd": None}
    try:
        if entry and sl and tp and sizing.get("final_quantity") and sizing.get("contract_size"):
            sign = 1.0 if str(side or "LONG").upper() == "LONG" else -1.0
            vol = float(sizing["final_quantity"])
            cs = float(sizing["contract_size"])
            out["sl_pnl_usd"] = round((float(sl) - float(entry)) * sign * vol * cs, 2)
            out["tp_pnl_usd"] = round((float(tp) - float(entry)) * sign * vol * cs, 2)
    except Exception:
        pass
    return out
