"""Hunt S2 extension/pullback timing only. No thesis/FIRE/order."""
from __future__ import annotations

EXECUTABLE_DISTANCE_ATR = 1.0
EXECUTABLE_DISTANCE_ATR_ACCEL_MULT = 1.3
EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT = 0.6
PULLBACK_ZONE_ATR_MIN = 0.25
PULLBACK_ZONE_ATR_MAX = 0.50
NEARBY_SR_ATR = 0.30
NEARBY_FVG_ATR = 0.30


def s2_executable(price, origin, atr15, mom, vol, sr, side) -> bool:
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
            name = "distance_to_resistance_atr" if side == "LONG" else "distance_to_support_atr"
            room = sr.measurement(name)
        except Exception:
            room = None

    blocked_by_sr = room is not None and room <= NEARBY_SR_ATR
    vol_tags = set(getattr(vol, "tags", None) or [])
    absorption = any("ABSORPTION" in str(t) for t in vol_tags)
    return (distance_atr <= threshold) and not blocked_by_sr and not absorption


def update_pullback(state: dict, price: float, atr15: float, sr, fvg, origin: float, side: str) -> bool:
    if state.get("extension_atr_ref") is None and atr15 > 0:
        state["extension_atr_ref"] = atr15
    if state.get("extension_price") is None:
        state["extension_price"] = price
    elif side == "LONG":
        state["extension_price"] = max(state["extension_price"], price)
    else:
        state["extension_price"] = min(state["extension_price"], price)

    if state.get("pullback_confirmed") or not state.get("extension_atr_ref"):
        return False

    extreme = state["extension_price"]
    retrace = (extreme - price) if side == "LONG" else (price - extreme)
    if retrace < PULLBACK_ZONE_ATR_MIN * state["extension_atr_ref"]:
        return False

    live_atr = atr15 if atr15 > 0 else state["extension_atr_ref"]
    near_origin = abs(price - origin) <= PULLBACK_ZONE_ATR_MAX * live_atr

    near_sr = False
    if sr is not None:
        try:
            name = "distance_to_support_atr" if side == "LONG" else "distance_to_resistance_atr"
            room = sr.measurement(name)
            near_sr = room is not None and room <= NEARBY_SR_ATR
        except Exception:
            near_sr = False

    near_fvg = False
    if fvg is not None:
        for lv in (getattr(fvg, "levels", None) or []):
            if lv.price is not None and abs(lv.price - price) <= NEARBY_FVG_ATR * live_atr:
                near_fvg = True
                break

    if near_origin or near_sr or near_fvg:
        state["pullback_confirmed"] = True
        return True
    return False
