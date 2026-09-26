"""S1 C watcher and S2 extension/pullback math."""
from __future__ import annotations
from typing import List
from ..contract import LONG, SHORT
from .entry_timing_c import find_m5_2_intrabar_entry

PULLBACK_ZONE_ATR_MIN = 0.25
PULLBACK_ZONE_ATR_MAX = 0.50
NEARBY_SR_ATR = 0.30
NEARBY_FVG_ATR = 0.30
EXECUTABLE_DISTANCE_ATR = 1.0
EXECUTABLE_DISTANCE_ATR_ACCEL_MULT = 1.3
EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT = 0.6


def _s2_executable_side(price, origin, atr15, mom, vol, sr, side) -> bool:
    if not origin or atr15 <= 0:
        return False
    distance_atr = abs(price - origin) / atr15
    tags = set(getattr(mom, "tags", None) or [])
    threshold = EXECUTABLE_DISTANCE_ATR
    if "ACCELERATING" in tags:
        threshold *= EXECUTABLE_DISTANCE_ATR_ACCEL_MULT
    elif "EXHAUSTING" in tags:
        threshold *= EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT
    room = None
    if sr is not None:
        try:
            name = "distance_to_resistance_atr" if side == LONG else "distance_to_support_atr"
            room = sr.measurement(name)
        except Exception:
            room = None
    blocked_by_sr = room is not None and room <= NEARBY_SR_ATR
    vol_tags = set(getattr(vol, "tags", None) or [])
    absorption = any("ABSORPTION" in str(t) for t in vol_tags)
    return (distance_atr <= threshold) and not blocked_by_sr and not absorption


def s2_update_pullback(state: dict, price: float, atr15: float, sr, fvg, origin: float, side: str) -> bool:
    if state.get("extension_atr_ref") is None and atr15 > 0:
        state["extension_atr_ref"] = atr15
    if state.get("extension_price") is None:
        state["extension_price"] = price
    else:
        if side == LONG:
            state["extension_price"] = max(state["extension_price"], price)
        else:
            state["extension_price"] = min(state["extension_price"], price)
    if state.get("pullback_confirmed") or not state.get("extension_atr_ref"):
        return False
    extreme = state["extension_price"]
    retrace = (extreme - price) if side == LONG else (price - extreme)
    if retrace < PULLBACK_ZONE_ATR_MIN * state["extension_atr_ref"]:
        return False
    live = atr15 if atr15 > 0 else state["extension_atr_ref"]
    near_origin = abs(price - origin) <= PULLBACK_ZONE_ATR_MAX * live
    near_sr = False
    if sr is not None:
        try:
            name = "distance_to_support_atr" if side == LONG else "distance_to_resistance_atr"
            room = sr.measurement(name)
            near_sr = room is not None and room <= NEARBY_SR_ATR
        except Exception:
            near_sr = False
    near_fvg = False
    if fvg is not None:
        for lv in (getattr(fvg, "levels", None) or []):
            if lv.price is not None and abs(lv.price - price) <= NEARBY_FVG_ATR * live:
                near_fvg = True
                break
    if near_origin or near_sr or near_fvg:
        state["pullback_confirmed"] = True
        return True
    return False


def start_c_watch(state: dict, path: str, m5_ts: int, level: float, direction: str) -> None:
    state["phase"] = "C_WATCH"
    state["path"] = path
    state["c_watch"] = {
        "path": path,
        "window_open_ts": int(m5_ts),
        "structural_level": float(level),
        "direction": direction,
        "checked": set(),
    }


def tick_c(state: dict, one_minute_candles: List[dict]) -> str:
    w = state.get("c_watch") or {}
    open_ts = w.get("window_open_ts")
    level = w.get("structural_level")
    side = w.get("direction")
    if open_ts is None or level is None or side not in (LONG, SHORT):
        return "NONE"
    window = [c for c in (one_minute_candles or []) if int(open_ts) <= int(c["ts"]) < int(open_ts) + 300]
    window.sort(key=lambda c: c["ts"])
    if not window:
        return "NONE"
    got = find_m5_2_intrabar_entry(side, float(level), int(open_ts), window)
    if got:
        w["result"] = got
        return "SUCCESS"
    last_1m = int(open_ts) + 4 * 60
    have = {int(c["ts"]) for c in window}
    if all((int(open_ts) + i * 60) in have for i in range(5)):
        return "MISS"
    if window and int(window[-1]["ts"]) >= last_1m and not got:
        return "MISS"
    return "NONE"
