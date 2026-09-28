"""S1 structure helpers — forming 15m, M5 lookback, C watch, S2 math."""
from __future__ import annotations
from typing import Any, List, Optional
from ..contract import LONG, SHORT
from ..structure.observe import observe as obs_structure
from .entry_timing_c import find_m5_2_intrabar_entry

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

def _dir(d):
    d = (d or "").upper()
    if d in ("BULLISH", "LONG", "UP"):
        return LONG
    if d in ("BEARISH", "SHORT", "DOWN"):
        return SHORT
    return None

def parent_open(ts5: int) -> int:
    return int(ts5) - (int(ts5) % 900)

def slot_of(ts5: int) -> int:
    return int(((int(ts5) % 900) // 300) + 1)

def forming_15m(candles_15m: List[dict], candles_5m: Optional[List[dict]]) -> List[dict]:
    rows = [dict(c) for c in (candles_15m or [])]
    if not candles_5m:
        return rows
    po = parent_open(candles_5m[-1]["ts"])
    live = [c for c in candles_5m if parent_open(c["ts"]) == po]
    if not live:
        return rows
    bar = {
        "ts": po,
        "open": live[0]["open"],
        "high": max(float(c["high"]) for c in live),
        "low": min(float(c["low"]) for c in live),
        "close": live[-1]["close"],
        "volume": sum(float(c.get("volume") or 0) for c in live),
    }
    if rows and int(rows[-1]["ts"]) == po:
        rows[-1] = bar
    elif not rows or int(rows[-1]["ts"]) < po:
        rows.append(bar)
    return rows


def _parent_swings(candles_15m: List[dict], lr: int):
    n = len(candles_15m)
    sh = sl = None
    for i in range(lr, max(lr, n - lr)):
        h, l = candles_15m[i]["high"], candles_15m[i]["low"]
        if all(h > candles_15m[i - k]["high"] and h >= candles_15m[i + k]["high"] for k in range(1, lr + 1)):
            sh = h
        if all(l < candles_15m[i - k]["low"] and l <= candles_15m[i + k]["low"] for k in range(1, lr + 1)):
            sl = l
    return sh, sl


def last_15m_break(candles_15m: List[dict]):
    if not candles_15m or len(candles_15m) < 30:
        return None
    try:
        st = obs_structure(candles_15m, "15m", pivot_window_override=M15_PIVOT_OVERRIDE)
    except Exception:
        return None
    last = None
    for ev in st.history or []:
        et = (ev.event_type or "").upper()
        if et in ("BOS", "CHOCH", "CHoCH"):
            last = ev
    return last


def event_key(event: Any) -> tuple:
    et = (getattr(event, "event_type", None) or "").upper()
    ts = int(getattr(event, "timestamp", 0) or 0)
    raw = getattr(event, "reference_price", None)
    if raw is None:
        raw = getattr(event, "price", None)
    lvl = round(float(raw or 0), 1)
    side = _dir(getattr(event, "direction", None))
    return (et, ts, lvl, side)


def m5_event_level(event: Any) -> Optional[float]:
    raw = getattr(event, "reference_price", None)
    if raw is None:
        raw = getattr(event, "price", None)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v else None


def m5_structure_events(candles_5m: List[dict]) -> list:
    if not candles_5m or len(candles_5m) < 30:
        return []
    try:
        st = obs_structure(candles_5m, "5m", pivot_window_override=M5_PIVOT_OVERRIDE)
    except Exception:
        return []
    return list(st.history or [])


def fresh_m5_event(events: list, direction: str, last_5m_ts: int, consumed: set):
    found = None
    for ev in events:
        et = (ev.event_type or "").upper()
        if et not in ("BOS", "CHOCH", "CHoCH"):
            continue
        if _dir(ev.direction) != direction:
            continue
        ts = ev.timestamp
        if ts is None or int(ts) != int(last_5m_ts):
            continue
        if event_key(ev) in consumed:
            continue
        if et in ("CHOCH", "CHoCH"):
            return ev
        if found is None:
            found = ev
    return found


def fresh_m5_event_s1(events: list, direction: str, last_5m_ts: int, consumed: set, thesis_invalid=None):
    lo = int(last_5m_ts) - S1_LOOKBACK_BARS * M5_BAR_SECONDS
    hi = int(last_5m_ts)
    inv = None
    try:
        inv = float(thesis_invalid) if thesis_invalid is not None else None
    except (TypeError, ValueError):
        inv = None
    last_opp = 0
    for ev in events:
        et = (ev.event_type or "").upper()
        if et not in ("BOS", "CHOCH", "CHoCH"):
            continue
        ts = ev.timestamp
        if ts is None:
            continue
        if _dir(ev.direction) != direction:
            last_opp = max(last_opp, int(ts))
    ranked = []
    for ev in events:
        et = (ev.event_type or "").upper()
        if et not in ("BOS", "CHOCH", "CHoCH"):
            continue
        if _dir(ev.direction) != direction:
            continue
        ts = ev.timestamp
        if ts is None:
            continue
        ts = int(ts)
        if ts < lo or ts > hi:
            continue
        if event_key(ev) in consumed:
            continue
        if last_opp > ts:
            continue
        lvl = m5_event_level(ev)
        if lvl is None:
            continue
        if inv is not None:
            if direction == LONG and lvl < inv:
                continue
            if direction == SHORT and lvl > inv:
                continue
        ranked.append((ts, 0 if et in ("CHOCH", "CHoCH") else 1, ev))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return ranked[0][2]
