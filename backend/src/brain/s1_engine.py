"""S1 / S2 engine — live decision spine.

15m forming BOS/CHoCH sets direction.
Slot 1/2 + M5 Lookback-5 same-direction BOS/CHoCH.
C = first CLOSED 1m through that M5 reference_price.
S2 = extension → measured pullback → new M5 → C.

Does not import Hunt. Does not take direction from Hunt.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT, NEUTRAL, STATE_WAIT
from ..indicators import arrays, atr as _atr
from ..structure.observe import observe as obs_structure
from .entry_timing_c import find_m5_2_intrabar_entry

S1_VERSION = "S1_S2_C"
SL_ATR = 1.5
TP_ATR = 2.5
M5_PIVOT_OVERRIDE = 2
M15_PIVOT_OVERRIDE = 2
S1_LOOKBACK_BARS = 5
M5_BAR_SECONDS = 300
PULLBACK_ZONE_ATR_MIN = 0.25
PULLBACK_ZONE_ATR_MAX = 0.50
NEARBY_SR_ATR = 0.30
NEARBY_FVG_ATR = 0.30
EXECUTABLE_DISTANCE_ATR = 1.0
EXECUTABLE_DISTANCE_ATR_ACCEL_MULT = 1.3
EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT = 0.6

_STATE: Dict[str, Any] = {}


def parent_open(ts5: int) -> int:
    return int(ts5) - (int(ts5) % 900)


def slot_of(ts5: int) -> int:
    return int(((int(ts5) % 900) // 300) + 1)


def reset_s1_state() -> None:
    _STATE.clear()


def _empty() -> Dict[str, Any]:
    return {
        "phase": "WAIT",
        "path": None,
        "direction": None,
        "origin_level": None,
        "thesis_invalid": None,
        "thesis_ts": None,
        "event": None,
        "consumed": set(),
        "c_watch": None,
        "extension_price": None,
        "extension_atr_ref": None,
        "pullback_confirmed": False,
    }


def _st() -> Dict[str, Any]:
    if not _STATE:
        _STATE.update(_empty())
    if not isinstance(_STATE.get("consumed"), set):
        _STATE["consumed"] = set(_STATE.get("consumed") or [])
    return _STATE


def _wipe() -> None:
    _STATE.clear()
    _STATE.update(_empty())
    _STATE["phase"] = "INVALID"


def _dir(d) -> Optional[str]:
    d = (d or "").upper()
    if d in ("BULLISH", "LONG", "UP"):
        return LONG
    if d in ("BEARISH", "SHORT", "DOWN"):
        return SHORT
    return None
