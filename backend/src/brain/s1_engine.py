"""S1 / S2 engine — live decision spine.
Does not import Hunt.
"""
from __future__ import annotations
from typing import Dict, List, Optional
from ..contract import LONG, SHORT, NEUTRAL, STATE_WAIT
from ..indicators import arrays, atr as _atr
from .s1_detect import (
    M15_PIVOT_OVERRIDE, event_key, forming_15m, fresh_m5_event, fresh_m5_event_s1,
    last_15m_break, m5_event_level, m5_structure_events, parent_open, slot_of,
    _dir, _parent_swings,
)
from .s1_timing import s2_update_pullback, start_c_watch, tick_c, _s2_executable_side

S1_VERSION = "S1_S2_C"
SL_ATR = 1.5
TP_ATR = 2.5
_STATE: Dict = {}

def reset_s1_state() -> None:
    _STATE.clear()

def _empty() -> Dict:
    return {"phase": "WAIT", "path": None, "direction": None, "origin_level": None,
            "thesis_invalid": None, "thesis_ts": None, "event": None, "consumed": set(),
            "c_watch": None, "extension_price": None, "extension_atr_ref": None,
            "pullback_confirmed": False}

def _st() -> Dict:
    if not _STATE:
        _STATE.update(_empty())
    if not isinstance(_STATE.get("consumed"), set):
        _STATE["consumed"] = set(_STATE.get("consumed") or [])
    return _STATE

def _wipe() -> None:
    _STATE.clear()
    _STATE.update(_empty())
    _STATE["phase"] = "INVALID"

def _atr15(candles_15m: List[dict]) -> float:
    if not candles_15m or len(candles_15m) < 16:
        return 0.0
    a = arrays(candles_15m)
    return float(_atr(a["high"], a["low"], a["close"], 14) or 0.0)

def _levels_for_fire(side: str, entry: float, origin: Optional[float], atr15: float) -> Dict:
    if not atr15:
        atr15 = abs(entry - float(origin or entry)) or 1.0
    if side == LONG:
        return {"entry": entry, "stop": entry - SL_ATR * atr15, "target": entry + TP_ATR * atr15, "atr_15m": atr15}
    return {"entry": entry, "stop": entry + SL_ATR * atr15, "target": entry - TP_ATR * atr15, "atr_15m": atr15}

def _wait(why: str, extra: Optional[Dict] = None) -> Dict:
    out = {"action": "WAIT", "state": STATE_WAIT, "direction": NEUTRAL, "entry_readiness": False,
           "why_state": [why], "blocking_reasons": [why], "brain_version": S1_VERSION,
           "size": "FULL", "ok": True, "timing": None, "timing_state": "WAIT", "slot": None, "event": None}
    if extra:
        out.update(extra)
    return out

def evaluate_s1(candles_15m, candle_5m, candles_5m=None, candles_1m=None, aux=None):
    aux = aux or {}
    rows5 = list(candles_5m or [])
    if candle_5m:
        rows5 = [c for c in rows5 if int(c.get("ts") or 0) <= int(candle_5m["ts"])]
        if not rows5 or int(rows5[-1]["ts"]) != int(candle_5m["ts"]):
            rows5 = rows5 + [candle_5m]
    form = forming_15m(candles_15m or [], rows5)
    if not form or not rows5:
        return _wait("Need 15m and 5m candles before S1 can look.")
    bar = rows5[-1]
    ts5 = int(bar["ts"]); price = float(bar["close"]); slot = slot_of(ts5)
    ev15 = last_15m_break(form)
    if ev15 is None:
        _wipe(); out = _wait("No forming 15m BOS/CHoCH — S1 has no side."); out["slot"] = slot; return out
    side = _dir(getattr(ev15, "direction", None))
    if side not in (LONG, SHORT):
        _wipe(); out = _wait("15m break has no LONG/SHORT direction."); out["slot"] = slot; return out
    origin = m5_event_level(ev15)
    ev_ts = getattr(ev15, "timestamp", None) or getattr(ev15, "detection_timestamp", None)
    lh, hl = _parent_swings(form, M15_PIVOT_OVERRIDE)
    inv = hl if side == LONG else lh
    st = _st()
    if st.get("direction") and st.get("direction") != side:
        _wipe(); st = _st()
    if inv is not None:
        try:
            inv_f = float(inv)
            if side == LONG and price < inv_f:
                _wipe(); return _wait("15m thesis invalidated — price through opposite swing.", {"slot": slot, "direction": side})
            if side == SHORT and price > inv_f:
                _wipe(); return _wait("15m thesis invalidated — price through opposite swing.", {"slot": slot, "direction": side})
        except (TypeError, ValueError):
            pass
    st["direction"] = side; st["origin_level"] = origin; st["thesis_invalid"] = inv
    st["thesis_ts"] = int(ev_ts) if ev_ts is not None else None
    st["event"] = ev15.event_type
    if st.get("phase") in ("WAIT", "INVALID", None):
        st["phase"] = "ARMED"; st["pullback_confirmed"] = False
        st["extension_price"] = None; st["extension_atr_ref"] = None; st["c_watch"] = None
    base = {"ok": True, "brain_version": S1_VERSION, "direction": side, "event": ev15.event_type,
            "thesis_ts": st.get("thesis_ts"), "thesis_level": origin, "thesis_invalid": inv,
            "slot": slot, "size": "FULL", "state": STATE_WAIT}
    if st.get("phase") == "C_WATCH":
        result = tick_c(st, candles_1m or [])
        if result == "SUCCESS":
            got = (st.get("c_watch") or {}).get("result") or {}
            entry = float(got.get("entry_price") or price)
            lv = _levels_for_fire(side, entry, origin, _atr15(form))
            path = (st.get("c_watch") or {}).get("path") or st.get("path") or "S1"
            st["phase"] = "ARMED"; st["c_watch"] = None
            out = _wait(f"S1/S2 {path} C 1m close", base); out.update(lv)
            out["action"] = "FIRE"; out["timing"] = path; out["timing_state"] = "FIRE"
            out["entry_readiness"] = True
            out["why_state"] = [f"{path} C first closed 1m through M5 line"]
            return out
        if result == "MISS":
            st["phase"] = "ARMED"; st["c_watch"] = None; st["path"] = None
            out = _wait("C miss — no 1m close through the M5 line this window.", base)
            out["timing_state"] = "ARMED"; out["timing_miss"] = True; return out
        out = _wait("C watching 1m closes through the M5 line.", base)
        out["timing_state"] = "C_WATCH"; out["timing"] = (st.get("c_watch") or {}).get("path"); return out
    events = m5_structure_events(rows5)
    ev_s1 = fresh_m5_event_s1(events, side, ts5, st["consumed"], inv)
    ev = fresh_m5_event(events, side, ts5, st["consumed"])
    if slot in (1, 2) and ev_s1 is not None and m5_event_level(ev_s1) is not None:
        start_c_watch(st, "S1", ts5, m5_event_level(ev_s1), side)
        st["consumed"].add(event_key(ev_s1))
        st["pullback_confirmed"] = False; st["extension_price"] = None; st["extension_atr_ref"] = None
        out = _wait("S1 armed — C watching this 5m.", base)
        out["timing_state"] = "C_WATCH"; out["timing"] = "S1"; return out
    mom, vol, sr, fvg = aux.get("mom"), aux.get("vol"), aux.get("sr"), aux.get("fvg")
    atr15 = _atr15(form)
    if st.get("phase") == "S2_EXTENDED":
        if origin:
            s2_update_pullback(st, price, atr15, sr, fvg, origin, side)
        if st.get("pullback_confirmed"):
            st["phase"] = "S2_PULLBACK"
        out = _wait("S2 measuring pullback.", base); out["timing"] = "S2"; out["timing_state"] = st["phase"]; return out
    if st.get("phase") == "S2_PULLBACK":
        if ev is not None and m5_event_level(ev) is not None:
            st["consumed"].add(event_key(ev)); start_c_watch(st, "S2", ts5, m5_event_level(ev), side)
            out = _wait("S2 fresh M5 — C watching.", base); out["timing_state"] = "C_WATCH"; out["timing"] = "S2"; return out
        out = _wait("S2 pullback done — waiting for a new same-direction 5m BOS/CHoCH.", base)
        out["timing"] = "S2"; out["timing_state"] = "S2_PULLBACK"; return out
    if ev is not None and origin and not _s2_executable_side(price, origin, atr15, mom, vol, sr, side):
        st["consumed"].add(event_key(ev)); st["phase"] = "S2_EXTENDED"
        st["extension_atr_ref"] = atr15 if atr15 > 0 else None; st["extension_price"] = price; st["pullback_confirmed"] = False
        out = _wait("S2 extension — too far to enter, measuring pullback.", base)
        out["timing"] = "S2"; out["timing_state"] = "S2_EXTENDED"; return out
    why = "15m thesis is on. Waiting for slot 1/2 M5 BOS/CHoCH (Lookback 5)."
    if slot == 3:
        why = "Slot 3 is outside the S1 window. Waiting for the next 15m."
    out = _wait(why, base); out["timing_state"] = st.get("phase") or "ARMED"; return out
