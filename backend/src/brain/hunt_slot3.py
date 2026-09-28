"""Hunt SLOT3 confirmed path: closed 15m event -> next 15m candle."""
from __future__ import annotations

import time
from typing import Any, List, Optional, Tuple

from ..contract import LONG, SHORT
from ..indicators import arrays, atr as _atr
from ..structure.observe import observe as obs_structure
from .s1_detect import parent_open, _dir


def latest_confirmed_15m_event(candles_15m: List[dict]) -> Optional[Any]:
    if not candles_15m or len(candles_15m) < 30:
        return None
    latest_ts = int(candles_15m[-1]["ts"])
    try:
        st = obs_structure(candles_15m, "15m", pivot_window_override=2)
    except Exception:
        return None
    current = []
    for ev in st.history or []:
        et = (getattr(ev, "event_type", None) or "").upper()
        if et not in ("BOS", "CHOCH"):
            continue
        ev_ts = getattr(ev, "timestamp", None) or getattr(ev, "detection_timestamp", None)
        if ev_ts is not None and int(ev_ts) == latest_ts:
            current.append(ev)
    return current[-1] if current else None


def next_candle_ready(event: Any, now_ts: Optional[int] = None) -> bool:
    if event is None:
        return False
    ev_ts = getattr(event, "timestamp", None)
    if ev_ts is None:
        return False
    now_ts = int(time.time()) if now_ts is None else int(now_ts)
    return parent_open(now_ts) == int(ev_ts) + 900


def atr15_closed(candles_15m: List[dict]) -> float:
    if len(candles_15m) < 16:
        return 0.0
    a = arrays(candles_15m)
    return float(_atr(a["high"], a["low"], a["close"], 14) or 0.0)


def fire_levels(side: str, entry: float, atr15: float) -> Tuple[float, float]:
    if atr15 <= 0:
        atr15 = max(abs(entry) * 0.001, 1.0)
    if side == LONG:
        return entry - 1.5 * atr15, entry + 2.5 * atr15
    return entry + 1.5 * atr15, entry - 2.5 * atr15


def slot3_candidate(
    candles_15m: List[dict],
    now_ts: Optional[int],
) -> Optional[Any]:
    ev = latest_confirmed_15m_event(candles_15m)
    if ev is None or not next_candle_ready(ev, now_ts):
        return None
    return ev


def side_of(event: Any) -> Optional[str]:
    return _dir(getattr(event, "direction", None))
