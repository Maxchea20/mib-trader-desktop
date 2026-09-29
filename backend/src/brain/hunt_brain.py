"""Single Hunt brain for S1/S2/SLOT3 timing.

Hunt owns thesis, direction, invalidation, FIRE authority and one ticket.
S1/S2/SLOT3 are timing paths only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT, NEUTRAL, STATE_WAIT, STATE_LONG, STATE_SHORT
from ..indicators import arrays, atr as _atr
from ..structure.observe import observe as obs_structure
from .hunt_c import start_c_watch, tick_c
from .hunt_s1 import current_forming_15m_event, same_parent_15m
from .hunt_s2 import s2_executable, update_pullback
from .hunt_slot3 import latest_confirmed_15m_event, next_candle_ready, side_of
from .s1_detect import event_key, forming_15m, m5_event_level, m5_structure_events, fresh_m5_event, parent_open, slot_of, _dir

VERSION = "HUNT_S1_S2_SLOT3_V1"
SL_ATR = 1.5
TP_ATR = 2.5

_STATE: Dict[str, Any] = {
    "phase": "WAIT",
    "thesis_id": None,
    "direction": None,
    "thesis_ts": None,
    "thesis_level": None,
    "thesis_invalid": None,
    "event": None,
    "objective_15m_ts": None,
    "fired": False,
    "consumed": set(),
    "c_watch": None,
    "path": None,
    "extension_price": None,
    "extension_atr_ref": None,
    "pullback_confirmed": False,
}


def reset_hunt_state() -> None:
    _STATE.clear()
    _STATE.update({
        "phase": "WAIT", "thesis_id": None, "direction": None,
        "thesis_ts": None, "thesis_level": None, "thesis_invalid": None,
        "event": None, "objective_15m_ts": None, "fired": False,
        "consumed": set(), "c_watch": None, "path": None,
        "extension_price": None, "extension_atr_ref": None,
        "pullback_confirmed": False,
    })


def _atr15(candles_15m: List[dict]) -> float:
    if len(candles_15m) < 16:
        return 0.0
    a = arrays(candles_15m)
    return float(_atr(a["high"], a["low"], a["close"], 14) or 0.0)


def _parent_swings(candles_15m: List[dict], lr: int = 2):
    n = len(candles_15m)
    sh = sl = None
    for i in range(lr, max(lr, n - lr)):
        h = float(candles_15m[i]["high"])
        l = float(candles_15m[i]["low"])
        if all(h > float(candles_15m[i-k]["high"]) and h >= float(candles_15m[i+k]["high"]) for k in range(1, lr+1)):
            sh = h
        if all(l < float(candles_15m[i-k]["low"]) and l <= float(candles_15m[i+k]["low"]) for k in range(1, lr+1)):
            sl = l
    return sh, sl


def _set_thesis(event: Any, candles_15m: List[dict], preserve_ticket: bool = False) -> None:
    side = side_of(event)
    if side not in (LONG, SHORT):
        return
    ts = getattr(event, "timestamp", None) or getattr(event, "detection_timestamp", None)
    level = m5_event_level(event)
    lh, hl = _parent_swings(candles_15m, 2)
    invalid = hl if side == LONG else lh
    _STATE["direction"] = side
    _STATE["thesis_ts"] = int(ts) if ts is not None else None
    _STATE["thesis_level"] = level
    _STATE["thesis_invalid"] = invalid
    _STATE["event"] = getattr(event, "event_type", None)
    _STATE["thesis_id"] = f"{_STATE['thesis_ts']}-{side}-{round(float(level or 0), 1)}"
    _STATE["objective_15m_ts"] = _STATE["thesis_ts"]
    if not preserve_ticket:
        _STATE["fired"] = False
    if not _STATE["fired"]:
        _STATE["phase"] = "ARMED"


def _invalidated(price: float) -> bool:
    side = _STATE.get("direction")
    inv = _STATE.get("thesis_invalid")
    if side == LONG and inv is not None and price < float(inv):
        return True
    if side == SHORT and inv is not None and price > float(inv):
        return True
    return False


def _levels(side: str, entry: float, atr15: float) -> Dict[str, float]:
    if not atr15:
        atr15 = max(abs(entry) * 0.001, 1.0)
    if side == LONG:
        return {"entry": entry, "stop": entry - SL_ATR * atr15, "target": entry + TP_ATR * atr15, "atr_15m": atr15}
    return {"entry": entry, "stop": entry + SL_ATR * atr15, "target": entry - TP_ATR * atr15, "atr_15m": atr15}


def _wait(reason: str, side: Optional[str], slot: Optional[int], timing_state: Optional[str] = None) -> Dict:
    return {
        "action": "WAIT",
        "state": STATE_WAIT,
        "direction": side or NEUTRAL,
        "entry_readiness": False,
        "why_state": [reason],
        "blocking_reasons": [reason],
        "brain_version": VERSION,
        "timing": None,
        "timing_state": timing_state or _STATE.get("phase") or "WAIT",
        "slot": slot,
        "event": _STATE.get("event"),
        "thesis_ts": _STATE.get("thesis_ts"),
        "thesis_level": _STATE.get("thesis_level"),
        "thesis_invalid": _STATE.get("thesis_invalid"),
        "thesis_id": _STATE.get("thesis_id"),
        "size": "FULL",
        "ok": True,
    }


def _fire(path: str, side: str, entry: float, atr15: float, slot: int, reason: str) -> Dict:
    levels = _levels(side, entry, atr15)
    _STATE["fired"] = True
    _STATE["phase"] = "FIRED"
    _STATE["path"] = path
    return {
        **levels,
        "action": "FIRE",
        "state": STATE_LONG if side == LONG else STATE_SHORT,
        "direction": side,
        "entry_readiness": True,
        "why_state": [reason],
        "blocking_reasons": [],
        "brain_version": VERSION,
        "timing": path,
        "timing_state": "FIRE",
        "slot": slot,
        "event": _STATE.get("event"),
        "thesis_ts": _STATE.get("thesis_ts"),
        "thesis_level": _STATE.get("thesis_level"),
        "thesis_invalid": _STATE.get("thesis_invalid"),
        "thesis_id": _STATE.get("thesis_id"),
        "size": "FULL",
        "ok": True,
    }


def _closed_thesis_event(candles_15m: List[dict]) -> Optional[Any]:
    return latest_confirmed_15m_event(candles_15m)


def evaluate_hunt(
    candles_15m: List[dict],
    candle_5m: Optional[dict],
    candles_5m: Optional[List[dict]] = None,
    candles_1m: Optional[List[dict]] = None,
    aux: Optional[Dict] = None,
    now_ts: Optional[int] = None,
) -> Dict:
    aux = aux or {}
    rows5 = list(candles_5m or [])
    if candle_5m:
        rows5 = [c for c in rows5 if int(c.get("ts", 0)) <= int(candle_5m["ts"])]
        if not rows5 or int(rows5[-1]["ts"]) != int(candle_5m["ts"]):
            rows5.append(candle_5m)
    if not candles_15m or not rows5:
        return _wait("Need 15m and 5m candles.", None, None)

    bar = rows5[-1]
    ts5 = int(bar["ts"])
    price = float(bar["close"])
    slot = slot_of(ts5)

    # A newer confirmed 15m event establishes/replaces the Hunt thesis.
    confirmed = _closed_thesis_event(candles_15m)
    if confirmed is not None:
        cts = int(getattr(confirmed, "timestamp", 0) or 0)
        cside = side_of(confirmed)
        same_objective = (
            _STATE.get("objective_15m_ts") is not None
            and int(_STATE.get("objective_15m_ts")) == cts
            and _STATE.get("direction") == cside
        )
        if _STATE.get("thesis_ts") != cts or _STATE.get("direction") != cside:
            _set_thesis(confirmed, candles_15m, preserve_ticket=same_objective)

    side = _STATE.get("direction")
    if side not in (LONG, SHORT):
        return _wait("No active Hunt thesis.", None, slot)

    if _invalidated(price):
        reset_hunt_state()
        return _wait("Hunt thesis invalidated.", None, slot, "INVALID")

    # One thesis can only produce one ticket.
    if _STATE.get("fired"):
        return _wait("Hunt thesis already fired — one ticket only.", side, slot, "FIRED")

    # SLOT3: confirmed event -> first tick of the NEXT 15m candle. No C.
    if confirmed is not None and next_candle_ready(confirmed, now_ts):
        cts = int(getattr(confirmed, "timestamp", 0) or 0)
        if cts == _STATE.get("thesis_ts"):
            entry = price
            atr15 = _atr15(candles_15m)
            return _fire("SLOT3", side, entry, atr15, slot, "Confirmed 15m CHoCH/BOS — first tick of next 15m candle.")

    # If a C watcher is already active, only C may resolve it.
    if _STATE.get("phase") == "C_WATCH":
        result = tick_c(_STATE, candles_1m or [])
        if result == "SUCCESS":
            got = (_STATE.get("c_watch") or {}).get("result") or {}
            entry = float(got.get("entry_price") or price)
            atr15 = _atr15(candles_15m)
            path = _STATE.get("path") or "S1"
            _STATE["c_watch"] = None
            return _fire(path, side, entry, atr15, slot, f"{path} C first qualifying 1m close.")
        if result == "MISS":
            _STATE["phase"] = "ARMED"
            _STATE["c_watch"] = None
            _STATE["path"] = None
            return _wait("C miss — no FIRE and no fallback.", side, slot, "ARMED")
        return _wait("C watching 1m closes.", side, slot, "C_WATCH")

    # S1: current forming 15m structural event only. No old last_15m_break.
    forming_event = current_forming_15m_event(candles_15m, rows5)
    if slot in (1, 2) and forming_event is not None:
        developing_side = _dir(getattr(forming_event, "direction", None))
        event_type = (getattr(forming_event, "event_type", None) or "").upper()
        if developing_side == side and event_type in ("BOS", "CHOCH") and same_parent_15m(forming_event, rows5):
            level = m5_event_level(forming_event)
            if level is not None:
                start_c_watch(
                    _STATE,
                    path="S1",
                    thesis_id=_STATE["thesis_id"],
                    m5_ts=ts5,
                    level=level,
                    direction=side,
                )
                _STATE["event"] = getattr(forming_event, "event_type", None)
                _STATE["objective_15m_ts"] = parent_open(ts5)
                return _wait("S1 qualified — current forming 15m CHoCH/BOS; C watching.", side, slot, "C_WATCH")

    # S2: extension -> pullback -> new same-direction 5m event -> C.
    events5 = m5_structure_events(rows5)
    ev5 = fresh_m5_event(events5, side, ts5, _STATE["consumed"])
    atr15 = _atr15(candles_15m)
    mom, sr = aux.get("mom"), aux.get("sr")

    if _STATE.get("phase") == "S2_EXTENDED":
        if _STATE.get("thesis_level") is not None:
            update_pullback(_STATE, price, atr15, sr, float(_STATE["thesis_level"]), side)
        if _STATE.get("pullback_confirmed"):
            _STATE["phase"] = "S2_PULLBACK"
        return _wait("S2 measuring pullback.", side, slot, _STATE["phase"])

    if _STATE.get("phase") == "S2_PULLBACK":
        if ev5 is not None and m5_event_level(ev5) is not None:
            _STATE["consumed"].add(event_key(ev5))
            start_c_watch(
                _STATE, path="S2", thesis_id=_STATE["thesis_id"],
                m5_ts=ts5, level=float(m5_event_level(ev5)), direction=side,
            )
            return _wait("S2 continuation M5 qualified — C watching.", side, slot, "C_WATCH")
        return _wait("S2 pullback confirmed — waiting for a new same-direction 5m event.", side, slot, "S2_PULLBACK")

    if ev5 is not None and _STATE.get("thesis_level") is not None:
        executable = s2_executable(price, float(_STATE["thesis_level"]), atr15, mom, sr, side)
        if not executable:
            _STATE["consumed"].add(event_key(ev5))
            _STATE["phase"] = "S2_EXTENDED"
            _STATE["extension_atr_ref"] = atr15 if atr15 > 0 else None
            _STATE["extension_price"] = price
            _STATE["pullback_confirmed"] = False
            return _wait("S2 extension — measuring pullback.", side, slot, "S2_EXTENDED")

    return _wait("Hunt thesis active — no S1/S2 timing qualification.", side, slot, _STATE.get("phase") or "ARMED")
