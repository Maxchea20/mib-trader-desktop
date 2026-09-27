"""S1/S2 + C timing inside Hunt. Not an engine. No Isolated. No second FIRE pipe."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT
from ..indicators import arrays, atr as _atr
from ..structure.observe import observe as obs_structure
from .entry_timing_c import find_m5_2_intrabar_entry
from .observation_hunt import SL_ATR, TP_ATR
from .observation_hunt_c import slot_of

M5_PIVOT_OVERRIDE = 2
PULLBACK_ZONE_ATR_MIN = 0.25
PULLBACK_ZONE_ATR_MAX = 0.50
NEARBY_SR_ATR = 0.30
NEARBY_FVG_ATR = 0.30
EXECUTABLE_DISTANCE_ATR = 1.0
EXECUTABLE_DISTANCE_ATR_ACCEL_MULT = 1.3
EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT = 0.6

_STATE: Dict[str, Any] = {}


def reset_timing_state() -> None:
    _STATE.clear()


def _empty() -> Dict[str, Any]:
    return {
        "phase": "WAIT",
        "path": None,
        "direction": None,
        "origin_level": None,
        "thesis_invalid": None,
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


def direction_from_state() -> Optional[str]:
    return _st().get("direction")


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


def _atr15(candles_15m: List[dict]) -> float:
    if not candles_15m or len(candles_15m) < 16:
        return 0.0
    a = arrays(candles_15m)
    return float(_atr(a["high"], a["low"], a["close"], 14) or 0.0)


def _levels_for_fire(side: str, entry: float, origin: Optional[float], atr15: float) -> Dict:
    level = float(origin or entry)
    if not atr15:
        atr15 = abs(entry - level) or 1.0
    if side == LONG:
        return {"entry": entry, "stop": level - SL_ATR * atr15, "target": level + TP_ATR * atr15, "atr_15m": atr15}
    return {"entry": entry, "stop": level + SL_ATR * atr15, "target": level - TP_ATR * atr15, "atr_15m": atr15}


def _invalidated(hunt: dict, price: float) -> bool:
    if hunt.get("armed") is False and not hunt.get("thesis_ts") and hunt.get("action") != "FIRE":
        if not hunt.get("gate"):
            return True
    inv = hunt.get("thesis_invalid")
    side = hunt.get("direction")
    if inv is None or side not in (LONG, SHORT):
        return False
    try:
        inv = float(inv)
    except (TypeError, ValueError):
        return False
    if side == LONG and price < inv:
        return True
    if side == SHORT and price > inv:
        return True
    return False


def tick_timing(
    hunt: dict,
    candles_15m: List[dict],
    candles_5m: List[dict],
    candles_1m: Optional[List[dict]] = None,
    aux: Optional[dict] = None,
) -> dict:
    aux = aux or {}
    st = _st()
    patch: Dict[str, Any] = {"timing_state": st.get("phase")}
    if not candles_5m:
        return patch
    bar = candles_5m[-1]
    ts5 = int(bar["ts"])
    price = float(bar["close"])
    slot = slot_of(ts5)
    side = hunt.get("direction")
    if side not in (LONG, SHORT):
        side = _dir(hunt.get("direction"))
    if _invalidated(hunt, price) or side not in (LONG, SHORT):
        if st.get("phase") not in ("WAIT", None):
            _wipe()
        patch["timing_state"] = "INVALID" if _STATE.get("phase") == "INVALID" else st.get("phase")
        return patch
    origin = hunt.get("thesis_level") or hunt.get("entry")
    try:
        origin = float(origin) if origin else None
    except (TypeError, ValueError):
        origin = None
    st["direction"] = side
    st["origin_level"] = origin
    st["thesis_invalid"] = hunt.get("thesis_invalid")
    if st.get("phase") in ("WAIT", "INVALID", None):
        st["phase"] = "ARMED"
        st["pullback_confirmed"] = False
        st["extension_price"] = None
        st["extension_atr_ref"] = None
        st["c_watch"] = None
    if hunt.get("action") == "FIRE" and slot == 3:
        pack = hunt.get("hunt") or {}
        path = (pack.get("m5_path") or hunt.get("v3a_path") or "")
        if "impulse" in str(path) or "hold_3" in str(path) or path == "impulse_3":
            patch["timing"] = "SLOT3"
            patch["timing_state"] = "FIRE"
            return patch
    if st.get("phase") == "C_WATCH":
        result = tick_c(st, candles_1m or [])
        if result == "SUCCESS":
            got = (st.get("c_watch") or {}).get("result") or {}
            entry = float(got.get("entry_price") or price)
            atr15 = _atr15(candles_15m)
            lv = _levels_for_fire(side, entry, origin, atr15)
            path = (st.get("c_watch") or {}).get("path") or st.get("path") or "S1"
            st["phase"] = "ARMED"
            st["c_watch"] = None
            patch.update(lv)
            patch["action"] = "FIRE"
            patch["timing"] = path
            patch["timing_state"] = "FIRE"
            patch["direction"] = side
            why = list(hunt.get("why_state") or [])
            patch["why_state"] = [f"Hunt timing {path} C 1m close"] + why
            return patch
        if result == "MISS":
            st["phase"] = "ARMED"
            st["c_watch"] = None
            st["path"] = None
            patch["timing_state"] = "ARMED"
            patch["timing_miss"] = True
            return patch
        patch["timing_state"] = "C_WATCH"
        patch["timing"] = (st.get("c_watch") or {}).get("path")
        return patch
    events = m5_structure_events(candles_5m)
    ev = fresh_m5_event(events, side, ts5, st["consumed"])
    s1_ok = slot in (1, 2) and ev is not None and m5_event_level(ev) is not None
    if s1_ok:
        lvl = m5_event_level(ev)
        st["consumed"].add(event_key(ev))
        start_c_watch(st, "S1", ts5, lvl, side)
        st["pullback_confirmed"] = False
        st["extension_price"] = None
        st["extension_atr_ref"] = None
        patch["timing_state"] = "C_WATCH"
        patch["timing"] = "S1"
        return patch
    mom, vol, sr, fvg = aux.get("mom"), aux.get("vol"), aux.get("sr"), aux.get("fvg")
    atr15 = _atr15(candles_15m)
    if st.get("phase") == "S2_EXTENDED":
        if origin:
            s2_update_pullback(st, price, atr15, sr, fvg, origin, side)
        if st.get("pullback_confirmed"):
            st["phase"] = "S2_PULLBACK"
        patch["timing_state"] = st["phase"]
        return patch
    if st.get("phase") == "S2_PULLBACK":
        if ev is not None and m5_event_level(ev) is not None:
            lvl = m5_event_level(ev)
            st["consumed"].add(event_key(ev))
            start_c_watch(st, "S2", ts5, lvl, side)
            patch["timing_state"] = "C_WATCH"
            patch["timing"] = "S2"
            return patch
        patch["timing_state"] = "S2_PULLBACK"
        return patch
    if ev is not None and origin and not _s2_executable_side(price, origin, atr15, mom, vol, sr, side):
        st["consumed"].add(event_key(ev))
        st["phase"] = "S2_EXTENDED"
        st["extension_atr_ref"] = atr15 if atr15 > 0 else None
        st["extension_price"] = price
        st["pullback_confirmed"] = False
        patch["timing_state"] = "S2_EXTENDED"
        return patch
    patch["timing_state"] = st.get("phase") or "ARMED"
    return patch


def apply_to_hunt(hunt: dict, patch: dict) -> dict:
    if not patch:
        return hunt
    if patch.get("action") == "FIRE":
        hunt["action"] = "FIRE"
        hunt["direction"] = patch.get("direction") or hunt.get("direction")
        hunt["entry"] = patch.get("entry")
        hunt["stop"] = patch.get("stop")
        hunt["target"] = patch.get("target")
        if patch.get("atr_15m"):
            hunt["atr_15m"] = patch["atr_15m"]
        if patch.get("why_state"):
            hunt["why_state"] = patch["why_state"]
        pack = dict(hunt.get("hunt") or {})
        pack["m5_path"] = f"timing_{patch.get('timing')}"
        hunt["hunt"] = pack
    if "timing" in patch:
        hunt["timing"] = patch["timing"]
    if "timing_state" in patch:
        hunt["timing_state"] = patch["timing_state"]
    if patch.get("timing_miss"):
        hunt["timing_miss"] = True
    return hunt
