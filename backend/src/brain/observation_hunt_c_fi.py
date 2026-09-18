"""Hunt C-FI — C-fast OR C-internal OR rearm of a still-valid 15m thesis.

Gate A — C-fast: swing 15m CHoCH or any BOS that is not extended.
Gate B — Internal: same events on 15m pivots L/R=2.
Gate C — Rearm: last valid 15m event is still in force (no opposite CHoCH),
         even if it printed on an earlier 15m. 5m may answer again.
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


def _parent_swings(candles_15m: List[dict], lr: int) -> Tuple[Optional[float], Optional[float]]:
    n = len(candles_15m)
    sh = sl = None
    for i in range(lr, max(lr, n - lr)):
        h, l = candles_15m[i]["high"], candles_15m[i]["low"]
        if all(h > candles_15m[i - k]["high"] and h >= candles_15m[i + k]["high"] for k in range(1, lr + 1)):
            sh = h
        if all(l < candles_15m[i - k]["low"] and l <= candles_15m[i + k]["low"] for k in range(1, lr + 1)):
            sl = l
    return sh, sl


def active_setup(
    candles_15m: List[dict],
    pivot: int = 2,
    require_fresh: bool = True,
) -> Optional[Tuple[str, str, Optional[int]]]:
    if not candles_15m or len(candles_15m) < 60:
        return None
    st = obs_structure(candles_15m, "15m", pivot_window_override=pivot)
    bar_ts = int(candles_15m[-1]["ts"])
    last = None
    bos_streak = 0
    streak_dir = None
    for ev in st.history or []:
        et = ev.event_type or ""
        if et in ("CHoCH", "CHOCH"):
            streak_dir = ev.direction
            bos_streak = 0
            last = ev
        elif et == "BOS":
            if streak_dir == ev.direction:
                bos_streak += 1
            else:
                streak_dir = ev.direction
                bos_streak = 1
            last = ev if bos_streak < 3 else None
    if last is None:
        return None
    ev_ts = last.timestamp or getattr(last, "detection_timestamp", None)
    if require_fresh and ev_ts is not None and int(ev_ts) != bar_ts:
        return None
    side = _dir(last.direction)
    if side not in (LONG, SHORT):
        return None
    return side, last.event_type, int(ev_ts) if ev_ts is not None else bar_ts


def internal_setup(candles_15m: List[dict]) -> Optional[Tuple[str, str]]:
    got = active_setup(candles_15m, pivot=2, require_fresh=True)
    if not got:
        return None
    return got[0], got[1]


def _attach_thesis(fire, *, side, event, gate, ev_ts, candles_15m, pivot, rearm):
    lh, hl = _parent_swings(candles_15m, pivot)
    hunt = fire.get("hunt") if isinstance(fire.get("hunt"), dict) else {}
    try:
        level = float(hunt.get("level") or fire.get("entry") or 0) or None
    except (TypeError, ValueError):
        level = None
    fire["thesis_ts"] = ev_ts
    fire["thesis_level"] = level
    fire["thesis_invalid"] = hl if side == LONG else lh
    fire["rearm"] = bool(rearm)
    fire["gate"] = gate
    fire["event"] = event or fire.get("event")
    return fire


def _try_answer(side, event, live, candles_15m, candle_5m, candles_4h, candles_5m, slot):
    if slot == 3:
        prior = candles_15m[:-1]
        v3 = evaluate_hunt_v3(prior, live, candles_4h=candles_4h)
        if v3.get("action") == "FIRE" and v3.get("direction") == side:
            path = (v3.get("hunt") or {}).get("m5_path") or "impulse_3"
            return _stamp(v3, path, event)
        return None
    v2_fill = evaluate_hunt(
        candles_15m, candle_5m, candles_4h=candles_4h, candles_5m=candles_5m,
    )
    if v2_fill.get("action") == "FIRE" and v2_fill.get("direction") == side:
        path = "v2_" + str((v2_fill.get("hunt") or {}).get("m5_path") or "clean")
        return _stamp(v2_fill, path, event)
    return None


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
        why = list(fast.get("why_state") or [])
        if why and why[0] in ("Hunt C", "Hunt C-fast"):
            why[0] = "Hunt C-FI cfast"
            fast["why_state"] = why
        got = active_setup(candles_15m, pivot=5, require_fresh=False)
        return _attach_thesis(
            fast, side=fast.get("direction"), event=fast.get("event"),
            gate="cfast", ev_ts=got[2] if got else None,
            candles_15m=candles_15m, pivot=5, rearm=False,
        )

    live = live_5ms or ([candle_5m] if candle_5m else [])
    slot = len(live) if live else (slot_of(candle_5m["ts"]) if candle_5m else None)
    wx = classify(candles_4h or [], candles_1h) if candles_4h else None
    ts = candle_5m.get("ts") if candle_5m else None

    def done(out, armed=False, event=None):
        out["brain_version"] = HUNT_VERSION_C_FI
        if "gate" not in out:
            out["gate"] = "internal"
        return _pulse(out, slot=slot, armed=armed, event=event, wx=wx, ts=ts)

    if not candles_15m or not candle_5m:
        return done(_wait("Need more candles before Hunt C-FI can look."), False)

    setup = internal_setup(candles_15m)
    if setup:
        side, event = setup
        if wx and not side_allowed(wx.get("flag"), side):
            way = "long" if side == LONG else "short"
            return done(_wait(
                f"4-hour weather is {wx.get('flag')}, so no {way} trade now."
            ), True, event)
        fire = _try_answer(side, event, live, candles_15m, candle_5m, candles_4h, candles_5m, slot)
        if fire:
            fire["brain_version"] = HUNT_VERSION_C_FI
            why = list(fire.get("why_state") or [])
            if why and why[0] == "Hunt C":
                why[0] = "Hunt C-FI internal"
                fire["why_state"] = why
            got = active_setup(candles_15m, pivot=2, require_fresh=True)
            fire = _attach_thesis(
                fire, side=side, event=event, gate="internal",
                ev_ts=got[2] if got else None, candles_15m=candles_15m, pivot=2, rearm=False,
            )
            return done(fire, True, event)
        if slot == 3:
            return done(_wait(
                "Internal setup is ready, but the third 5-minute candle did not close through the last 15-minute high or low."
            ), True, event)
        return done(_wait(
            "Internal 15m setup is on. This 5-minute candle did not tap the level yet."
        ), True, event)

    lingering = active_setup(candles_15m, pivot=5, require_fresh=False) or active_setup(
        candles_15m, pivot=2, require_fresh=False
    )
    if not lingering:
        fast["brain_version"] = HUNT_VERSION_C_FI
        fast["gate"] = None
        return fast

    side, event, ev_ts = lingering
    pivot = 5 if active_setup(candles_15m, pivot=5, require_fresh=False) else 2
    gate = "rearm_cfast" if pivot == 5 else "rearm_internal"
    if wx and not side_allowed(wx.get("flag"), side):
        way = "long" if side == LONG else "short"
        out = _wait(f"4-hour weather is {wx.get('flag')}, so no {way} re-entry now.")
        out["gate"] = gate
        return done(out, True, event)
    fire = _try_answer(side, event, live, candles_15m, candle_5m, candles_4h, candles_5m, slot)
    if fire:
        fire["brain_version"] = HUNT_VERSION_C_FI
        why = list(fire.get("why_state") or [])
        if why and why[0] in ("Hunt C", "Hunt C-fast"):
            why[0] = "Hunt C-FI rearm"
            fire["why_state"] = why
        else:
            fire["why_state"] = ["Hunt C-FI rearm"] + why
        fire = _attach_thesis(
            fire, side=side, event=event, gate=gate,
            ev_ts=ev_ts, candles_15m=candles_15m, pivot=pivot, rearm=True,
        )
        return done(fire, True, event)
    out = _wait("15m thesis is still valid. Waiting for the 5-minute candle to answer again.")
    out["gate"] = gate
    return done(out, True, event)
