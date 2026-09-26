"""AI wake-up detector — S1/S2 live events."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

EVENT_REVIEW_15M = "REVIEW_15M"
EVENT_SETUP_ARMED = "SETUP_ARMED"
EVENT_FIRE = "FIRE"
EVENT_EXIT = "EXIT"
EVENT_PULLBACK = "PULLBACK_TO_LEVEL"
EVENT_THESIS_WEAK = "THESIS_WEAK"
EVENT_THESIS_INVALID = "THESIS_INVALID"

_LAST_FP: Optional[Tuple] = None


def reset() -> None:
    global _LAST_FP
    _LAST_FP = None


def _s1(state: Dict) -> Dict:
    return state.get("last_s1") or {}


def _lifecycle(state: Dict) -> Dict:
    return state.get("last_lifecycle") or {}


def _action(state: Dict) -> str:
    return str(state.get("last_action") or "")


def fingerprint(state: Dict) -> Tuple:
    h = _s1(state)
    lc = _lifecycle(state)
    return (
        h.get("action"),
        h.get("direction"),
        h.get("event"),
        h.get("thesis_ts"),
        h.get("gate"),
        _action(state),
        lc.get("action"),
        lc.get("exit_kind"),
    )


def classify(prev: Optional[Tuple], curr: Tuple, state: Dict) -> Optional[str]:
    if prev is not None and curr == prev:
        return None
    h = _s1(state)
    lc = _lifecycle(state)
    action = _action(state)
    prev_h = prev[0] if prev else None
    prev_lc = prev[6] if prev else None
    prev_action = prev[5] if prev else ""

    if h.get("action") == "FIRE" and prev_h != "FIRE":
        return EVENT_FIRE
    if str(action).upper().startswith("LIVE OPEN") and not str(prev_action).upper().startswith("LIVE OPEN"):
        return EVENT_FIRE
    if (lc.get("action") == "EXIT" or action.upper().startswith("LIFECYCLE_EXIT")) and not (
        prev_lc == "EXIT" or str(prev_action).upper().startswith("LIFECYCLE_EXIT")
    ):
        return EVENT_EXIT
    if h.get("thesis_invalid") and prev is not None:
        return EVENT_THESIS_INVALID
    return None


def inspect_and_maybe_emit(state: Dict) -> Optional[Dict]:
    global _LAST_FP
    fp = fingerprint(state)
    kind = classify(_LAST_FP, fp, state)
    _LAST_FP = fp
    if not kind:
        return None
    h = _s1(state)
    return {
        "kind": kind,
        "fingerprint": fp,
        "direction": h.get("direction"),
        "thesis_ts": h.get("thesis_ts"),
        "thesis_level": h.get("thesis_level"),
        "event": h.get("event"),
        "gate": h.get("gate"),
        "last_action": _action(state),
        "lifecycle": _lifecycle(state),
    }
