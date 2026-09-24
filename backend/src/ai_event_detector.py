"""Deterministic AI wake-up detector.

Sits AFTER autotrader.evaluate() in the existing 5s loop. Reads autotrader
STATE only. Never calls OpenAI. Never changes Hunt / Scenario / orders.

Emits at most one event per meaningful fingerprint change.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

EVENT_REVIEW_15M = "REVIEW_15M"
EVENT_SETUP_ARMED = "SETUP_ARMED"
EVENT_FIRE = "FIRE"
EVENT_ORDER_FAILED = "ORDER_FAILED"
EVENT_EXIT = "EXIT"
EVENT_PULLBACK = "PULLBACK_TO_LEVEL"
EVENT_THESIS_WEAK = "THESIS_WEAK"
EVENT_THESIS_INVALID = "THESIS_INVALID"

_LAST_FP: Optional[Tuple] = None


def reset() -> None:
    global _LAST_FP
    _LAST_FP = None


def _hunt(state: Dict) -> Dict:
    return state.get("last_hunt") or {}


def _scenario(state: Dict) -> Dict:
    return state.get("last_scenario_result") or {}


def _lifecycle(state: Dict) -> Dict:
    return state.get("last_lifecycle") or {}


def _action(state: Dict) -> str:
    return str(state.get("last_action") or "")


def fingerprint(state: Dict) -> Tuple:
    h = _hunt(state)
    sc = _scenario(state)
    lc = _lifecycle(state)
    invalid = bool(h.get("thesis_invalid"))
    return (
        h.get("action"),
        h.get("direction"),
        h.get("event"),
        h.get("thesis_ts"),
        h.get("thesis_level"),
        invalid,
        bool(h.get("rearm")),
        _action(state),
        sc.get("action"),
        sc.get("thesis_id"),
        sc.get("m5_slot"),
        lc.get("action"),
        lc.get("exit_kind"),
        state.get("last_fired_5m_ts"),
    )


def _is_fire_action(action: str) -> bool:
    """FIRE means the scenario engine actually opened a trade (live on
    MEXC, or a paper fill). The Hunt C-FI action is deliberately ignored:
    it never opens a trade, so showing its FIRE here only misleads."""
    a = (action or "").upper()
    return a.startswith("SCENARIO OPEN") or a.startswith("SCENARIO LIVE OPEN")


def _is_order_failed(action: str) -> bool:
    """Scenario engine fired but the order did not go through."""
    a = (action or "").upper()
    return a.startswith("SCENARIO LIVE ORDER FAILED") or a.startswith("SCENARIO FIRE BUT SIZING FAILED")


def _is_exit_action(action: str, lc_action: Optional[str]) -> bool:
    a = (action or "").upper()
    if lc_action == "EXIT":
        return True
    return a.startswith("LIFECYCLE_EXIT")


def _looks_pullback(hunt: Dict, action: str) -> bool:
    ev = str(hunt.get("event") or "").lower()
    why = str(hunt.get("why") or "").lower()
    blob = ev + " " + why + " " + action.lower()
    keys = ("tap", "pull", "retrace", "retest", "wick", "touch")
    return any(k in blob for k in keys)


def _looks_armed(hunt: Dict, action: str) -> bool:
    if hunt.get("rearm"):
        return True
    blob = (str(hunt.get("event") or "") + " " + str(hunt.get("why") or "") + " " + action).lower()
    return any(k in blob for k in ("arm", "armed", "watching", "setup"))


def _looks_weak(hunt: Dict, action: str) -> bool:
    a = (action or "").upper()
    if a.startswith("WEATHER_BLOCK"):
        return True
    blob = (str(hunt.get("why") or "") + " " + str(hunt.get("event") or "")).lower()
    return any(k in blob for k in ("weak", "fail to extend", "failed to extend", "roll over", "deteriorat"))


def classify(prev: Optional[Tuple], curr: Tuple, state: Dict) -> Optional[str]:
    """Return an event kind only when the transition is meaningful."""
    if prev is not None and curr == prev:
        return None

    h = _hunt(state)
    sc = _scenario(state)
    lc = _lifecycle(state)
    action = _action(state)

    prev_action = prev[7] if prev else ""
    prev_invalid = prev[5] if prev else False
    prev_lc = prev[11] if prev else None

    if _is_fire_action(action) and not _is_fire_action(prev_action):
        return EVENT_FIRE

    if _is_order_failed(action) and not _is_order_failed(prev_action):
        return EVENT_ORDER_FAILED

    if _is_exit_action(action, lc.get("action")) and not _is_exit_action(prev_action, prev_lc):
        return EVENT_EXIT

    if bool(h.get("thesis_invalid")) and not prev_invalid:
        return EVENT_THESIS_INVALID

    # After a process restart, skip soft events so we do not narrate
    # the already-current WAIT/watching state as a fresh change.
    if prev is None:
        return None

    if _looks_weak(h, action) and (curr[7] != prev[7] or curr[2] != prev[2]):
        return EVENT_THESIS_WEAK

    if _looks_pullback(h, action) and (curr[2] != prev[2] or curr[7] != prev[7]):
        return EVENT_PULLBACK

    if _looks_armed(h, action) and (curr[6] != prev[6] or curr[2] != prev[2]):
        return EVENT_SETUP_ARMED

    return None


def inspect_and_maybe_emit(state: Dict) -> Optional[Dict]:
    """Compare fingerprints. Return event payload or None.

    Always advances the stored fingerprint so a sticky FIRE does not
    re-emit every 5 seconds.
    """
    global _LAST_FP
    fp = fingerprint(state)
    kind = classify(_LAST_FP, fp, state)
    _LAST_FP = fp
    if not kind:
        return None
    h = _hunt(state)
    sc = _scenario(state)
    return {
        "kind": kind,
        "fingerprint": fp,
        "hunt_event": h.get("event"),
        "hunt_why": h.get("why"),
        "direction": h.get("direction") or sc.get("direction"),
        "thesis_ts": h.get("thesis_ts") or sc.get("origin_ts"),
        "thesis_level": h.get("thesis_level") or sc.get("origin_level"),
        "thesis_id": sc.get("thesis_id"),
        "m5_slot": sc.get("m5_slot"),
        "scenario_action": sc.get("action"),
        "last_action": _action(state),
        "lifecycle": _lifecycle(state),
    }
