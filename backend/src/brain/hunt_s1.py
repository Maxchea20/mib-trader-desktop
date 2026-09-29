"""Hunt S1 early path: current forming 15m + current 5m + C."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..contract import LONG, SHORT
from ..structure.observe import observe as obs_structure
from .s1_detect import (
    M15_PIVOT_OVERRIDE,
    forming_15m,
    m5_structure_events,
    fresh_m5_event,
    m5_event_level,
    event_key,
    parent_open,
    slot_of,
    _dir,
)


def current_forming_15m_event(
    candles_15m: List[dict],
    candles_5m: List[dict],
) -> Optional[Any]:
    """Return a BOS/CHoCH that belongs to the CURRENT forming 15m only."""
    if not candles_15m or not candles_5m:
        return None
    forming = forming_15m(candles_15m, candles_5m)
    if not forming:
        return None
    parent_ts = int(forming[-1]["ts"])
    try:
        st = obs_structure(
            forming,
            "15m",
            pivot_window_override=M15_PIVOT_OVERRIDE,
        )
    except Exception:
        return None

    current = []
    for ev in st.history or []:
        et = (getattr(ev, "event_type", None) or "").upper()
        if et not in ("BOS", "CHOCH"):
            continue
        ev_ts = getattr(ev, "timestamp", None) or getattr(ev, "detection_timestamp", None)
        if ev_ts is None or int(ev_ts) != parent_ts:
            continue
        current.append(ev)

    return current[-1] if current else None


def developing_event_for_thesis(
    candles_15m: List[dict],
    candles_5m: List[dict],
    thesis_direction: str,
) -> Optional[Any]:
    ev = current_forming_15m_event(candles_15m, candles_5m)
    if ev is None:
        return None
    if (getattr(ev, "event_type", None) or "").upper() not in ("BOS", "CHOCH"):
        return None
    if _dir(getattr(ev, "direction", None)) != thesis_direction:
        return None
    return ev


def qualify_s1(
    candles_15m: List[dict],
    candles_5m: List[dict],
    thesis_direction: str,
    consumed: set,
) -> Optional[Tuple[Any, Any]]:
    """S1 = current forming 15m developing CHoCH/BOS + current M5 same-direction BOS."""
    if not candles_5m:
        return None

    bar = candles_5m[-1]
    ts5 = int(bar["ts"])
    slot = slot_of(ts5)
    if slot not in (1, 2):
        return None

    ev15 = developing_event_for_thesis(candles_15m, candles_5m, thesis_direction)
    if ev15 is None:
        return None

    events5 = m5_structure_events(candles_5m)
    ev5 = fresh_m5_event(events5, thesis_direction, ts5, consumed)
    if ev5 is None:
        return None
    if (getattr(ev5, "event_type", None) or "").upper() not in ("BOS", "CHOCH"):
        return None

    level = m5_event_level(ev5)
    if level is None:
        return None

    return ev15, ev5


def forming_parent_ts(candles_5m: List[dict]) -> Optional[int]:
    return parent_open(candles_5m[-1]["ts"]) if candles_5m else None


def same_parent_15m(event: Any, candles_5m: List[dict]) -> bool:
    parent_ts = forming_parent_ts(candles_5m)
    ev_ts = getattr(event, "timestamp", None)
    return parent_ts is not None and ev_ts is not None and int(ev_ts) == int(parent_ts)
