"""Scenario SL/TP preview for the live-controls boxes."""
from typing import Dict, Optional, Tuple

from .market_data import data_access as dao
from .autotrader_state import CONFIG, STATE


def scenario_levels() -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str], Optional[float]]:
    sc = STATE.get("last_scenario_result") or {}
    log = STATE.get("last_scenario_trade_log") or {}
    entry = sc.get("entry") or log.get("actual_entry_price") or log.get("c_intended_price") or sc.get("origin_level")
    side = sc.get("direction") or log.get("direction")
    atr = sc.get("atr15") or log.get("atr15")
    if entry is None:
        try:
            bars = dao.read_closed_candles(CONFIG.get("timeframe", "15m"), limit=1)
            if bars:
                entry = float(bars[-1]["close"])
        except Exception:
            entry = None
    if atr is None:
        try:
            from .indicators import arrays, atr as calc_atr
            bars = dao.read_closed_candles("15m", limit=40)
            if len(bars) >= 20:
                aa = arrays(bars)
                atr = float(calc_atr(aa["high"], aa["low"], aa["close"], 14) or 0)
        except Exception:
            atr = None
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
