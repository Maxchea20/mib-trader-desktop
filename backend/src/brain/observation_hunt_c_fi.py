"""Hunt C-FI — C-fast OR C-internal, two separate IFs.

Gate A — C-fast: swing 15m CHoCH or any BOS that is not extended.
Gate B — Internal: same events on 15m pivots L/R=2.
Fill is unchanged: 5m #3 through prior 15m high/low, or V2 tap on 5m #1/#2,
level fill, 4h weather must allow, SL 1.5 / TP 2.5.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..contract import LONG, SHORT
from ..structure.observe import observe as obs_structure
from .observation_hunt import evaluate_hunt
from .observation_hunt_c import (
    _pulse,
    _stamp,
    _wait,
    parent_open,
    slot_of,
)
from .observation_hunt_c_fast import evaluate_hunt_c_fast
from .observation_hunt_v3 import evaluate_hunt_v3
from .weather import classify, side_allowed

HUNT_VERSION_C_FI = "OBSERVATION_HUNT_M5_C_FI"


def _dir(d):
    d = (d or "").upper()
    if d in ("BULLISH", "LONG", "UP"):
        return LONG
    if d in ("BEARISH", "SHORT", "DOWN"):
        return SHORT
    return None


def internal_setup(candles_15m: List[dict]) -> Optional[Tuple[str, str]]:
    if not candles_15m or len(candles_15m) < 60:
        return None
    st = obs_structure(candles_15m, "15m", pivot_window_override=2)
    bar_ts = int(candles_15m[-1]["ts"])
    fresh = []
    for e in st.history or []:
        ts = e.timestamp or getattr(e, "detection_timestamp", None)
        if ts is None or int(ts) != bar_ts:
            continue
        if (e.event_type or "") not in ("BOS", "CHoCH", "CHOCH"):
            continue
        fresh.append(e)
    if not fresh:
        return None
    bos_streak = 0
    streak_dir = None
    for ev in st.history or []:
        et = ev.event_type
        if et in ("CHoCH", "CHOCH"):
            streak_dir = ev.direction
            bos_streak = 0
        elif et == "BOS":
            if streak_dir == ev.direction:
                bos_streak += 1
            else:
                streak_dir = ev.direction
                bos_streak = 1
    last = fresh[-1]
    if last.event_type == "BOS" and bos_streak >= 3:
        return None
    side = _dir(last.direction)
    if side not in (LONG, SHORT):
        return None
    return side, last.event_type


def evaluate_hunt_c_fi(
    candles_15m: List[dict],
    candle_5m: dict,
    live_5ms: Optional[List[dict]] = None,
    candles_4h: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
) -> Dict:
    fast = evaluate_hunt_c_fast(
        candles_15m,
        candle_5m,
        live_5ms=live_5ms,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_5m=candles_5m,
    )
    if fast.get("action") == "FIRE":
        fast["brain_version"] = HUNT_VERSION_C_FI
        fast["gate"] = "cfast"
        why = list(fast.get("why_state") or [])
        if why and why[0] in ("Hunt C", "Hunt C-fast"):
            why[0] = "Hunt C-FI cfast"
            fast["why_state"] = why
        return fast

    live = live_5ms or ([candle_5m] if candle_5m else [])
    slot = len(live) if live else (slot_of(candle_5m["ts"]) if candle_5m else None)
    wx = classify(candles_4h or [], candles_1h) if candles_4h else None
    ts = candle_5m.get("ts") if candle_5m else None

    def done(out, armed=False, event=None):
        out["brain_version"] = HUNT_VERSION_C_FI
        out["gate"] = "internal"
        return _pulse(out, slot=slot, armed=armed, event=event, wx=wx, ts=ts)

    if not candles_15m or not candle_5m:
        return done(_wait("Need more candles before Hunt C-FI can look."), False)

    setup = internal_setup(candles_15m)
    if not setup:
        # keep C-fast wait text so the panel still explains the swing book
        fast["brain_version"] = HUNT_VERSION_C_FI
        fast["gate"] = None
        return fast

    side, event = setup
    if wx and not side_allowed(wx.get("flag"), side):
        way = "long" if side == LONG else "short"
        return done(_wait(
            f"4-hour weather is {wx.get('flag')}, so no {way} trade now."
        ), True, event)

    if slot == 3:
        prior = candles_15m[:-1]
        v3 = evaluate_hunt_v3(prior, live, candles_4h=candles_4h)
        if v3.get("action") == "FIRE" and v3.get("direction") == side:
            path = (v3.get("hunt") or {}).get("m5_path") or "impulse_3"
            fire = _stamp(v3, path, event)
            fire["brain_version"] = HUNT_VERSION_C_FI
            fire["gate"] = "internal"
            why = list(fire.get("why_state") or [])
            if why and why[0] == "Hunt C":
                why[0] = "Hunt C-FI internal"
                fire["why_state"] = why
            return done(fire, True, event)
        return done(_wait(
            "Internal setup is ready, but the third 5-minute candle did not close through the last 15-minute high or low."
        ), True, event)

    v2_fill = evaluate_hunt(
        candles_15m, candle_5m, candles_4h=candles_4h, candles_5m=candles_5m,
    )
    if v2_fill.get("action") == "FIRE" and v2_fill.get("direction") == side:
        path = "v2_" + str((v2_fill.get("hunt") or {}).get("m5_path") or "clean")
        fire = _stamp(v2_fill, path, event)
        fire["brain_version"] = HUNT_VERSION_C_FI
        fire["gate"] = "internal"
        return done(fire, True, event)
    return done(_wait(
        "Internal 15m setup is on. This 5-minute candle did not tap the level yet."
    ), True, event)
