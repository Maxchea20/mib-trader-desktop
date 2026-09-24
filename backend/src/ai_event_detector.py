"""AI wake-up detector — Scenario only.

Hunt C-FI may still be computed for the chart. It must NOT stamp FIRE
on the Event Stream while entry_engine is scenario.
"""
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


def _sc(state: Dict) -> Dict:
    return state.get("last_scenario_result") or {}


def _lifecycle(state: Dict) -> Dict:
    return state.get("last_lifecycle") or {}


def _action(state: Dict) -> str:
    return str(state.get("last_action") or "")


def fingerprint(state: Dict) -> Tuple:
    sc = _sc(state)
    lc = _lifecycle(state)
    return (
        sc.get("action"),
        sc.get("setup"),
        sc.get("scenario"),
        sc.get("thesis_id"),
        sc.get("direction"),
        sc.get("m5_slot"),
        sc.get("reason"),
        sc.get("provisional"),
        _action(state),
        lc.get("action"),
        lc.get("exit_kind"),
    )


def _is_fire(action: str, sc_action: Optional[str]) -> bool:
    if sc_action == "FIRE":
        return True
    a = (action or "").upper()
    return a.startswith("SCENARIO OPEN") or a.startswith("SCENARIO LIVE OPEN")


def classify(prev: Optional[Tuple], curr: Tuple, state: Dict) -> Optional[str]:
    if prev is not None and curr == prev:
        return None
    sc = _sc(state)
    lc = _lifecycle(state)
    action = _action(state)
    prev_action = prev[8] if prev else ""
    prev_sc = prev[0] if prev else None
    prev_lc = prev[9] if prev else None

    if _is_fire(action, sc.get("action")) and not _is_fire(prev_action, prev_sc):
        return EVENT_FIRE
    if (lc.get("action") == "EXIT" or action.upper().startswith("LIFECYCLE_EXIT")) and not (
        prev_lc == "EXIT" or str(prev_action).upper().startswith("LIFECYCLE_EXIT")
    ):
        return EVENT_EXIT
    if sc.get("action") in ("CANCEL", "SKIPPED") and prev_sc not in ("CANCEL", "SKIPPED"):
        return EVENT_THESIS_INVALID
    if prev is None:
        return None
    reason = str(sc.get("reason") or "").lower()
    if "c watching" in reason or "watching 1m" in reason:
        if curr[6] != (prev[6] if prev else None):
            return EVENT_SETUP_ARMED
    if "pullback" in reason or sc.get("scenario") == "PULLBACK_WATCH":
        if curr[2] != (prev[2] if prev else None):
            return EVENT_PULLBACK
    if sc.get("thesis_id") and prev and not prev[3]:
        return EVENT_SETUP_ARMED
    return None


def inspect_and_maybe_emit(state: Dict) -> Optional[Dict]:
    global _LAST_FP
    fp = fingerprint(state)
    kind = classify(_LAST_FP, fp, state)
    _LAST_FP = fp
    if not kind:
        return None
    sc = _sc(state)
    return {
        "kind": kind,
        "fingerprint": fp,
        "direction": sc.get("direction"),
        "thesis_ts": sc.get("origin_ts"),
        "thesis_level": sc.get("origin_level"),
        "thesis_id": sc.get("thesis_id"),
        "m5_slot": sc.get("m5_slot"),
        "scenario_action": sc.get("action"),
        "setup": sc.get("setup"),
        "reason": sc.get("reason"),
        "last_action": _action(state),
        "lifecycle": _lifecycle(state),
    }
