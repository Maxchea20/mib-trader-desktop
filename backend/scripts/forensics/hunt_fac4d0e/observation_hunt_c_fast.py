"""Hunt C-fast — C plus continuation BOS.

Same C door (5m #3 / level fill / weather / no 1% sleeve / no BREAKOUT),
but the 15m setup may be:
  - CHoCH, or
  - any BOS that is not extended (BOS #3+ skipped)

Lives on branch hunt-c-fast. Main stays on C.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import observation_hunt_c as cmod
from .observation_hunt_c import evaluate_hunt_c

HUNT_VERSION_C_FAST = "OBSERVATION_HUNT_M5_C_FAST"


def structure_ok_fast(v2: Dict) -> bool:
    hunt = v2.get("hunt") or {}
    ev = (hunt.get("event") or v2.get("event") or "").upper()
    bq = v2.get("bos_quality") or {}
    ext = bool(hunt.get("extended_bos") or bq.get("extended_bos"))
    if ev in ("CHOCH", "CHoCH"):
        return True
    if ev == "BOS" and not ext:
        return True
    return False


def evaluate_hunt_c_fast(
    candles_15m: List[dict],
    candle_5m: dict,
    live_5ms: Optional[List[dict]] = None,
    candles_4h: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
) -> Dict:
    old = cmod.structure_ok
    cmod.structure_ok = structure_ok_fast
    try:
        out = evaluate_hunt_c(
            candles_15m,
            candle_5m,
            live_5ms=live_5ms,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
        )
    finally:
        cmod.structure_ok = old
    out["brain_version"] = HUNT_VERSION_C_FAST
    why = list(out.get("why_state") or [])
    if why and why[0] == "Hunt C":
        why[0] = "Hunt C-fast"
        out["why_state"] = why
    return out
