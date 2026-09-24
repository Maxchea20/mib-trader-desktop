"""Detector only — no OpenAI, no Hunt rewrite."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ai_event_detector as d


def _state(**kw):
    base = {
        "last_hunt": {
            "action": "WAIT",
            "direction": "LONG",
            "event": None,
            "thesis_ts": 1,
            "thesis_level": 100.0,
            "thesis_invalid": False,
            "rearm": False,
            "why": "watching",
        },
        "last_scenario_result": {},
        "last_lifecycle": {},
        "last_action": "NO-TRADE (WAIT)",
        "last_fired_5m_ts": None,
    }
    base.update(kw)
    return base


def setup_function():
    d.reset()


def test_no_event_on_identical_ticks():
    s = _state()
    assert d.inspect_and_maybe_emit(s) is None  # first tick seeds fingerprint
    assert d.inspect_and_maybe_emit(s) is None
    assert d.inspect_and_maybe_emit(s) is None


def test_fire_emits_once():
    d.inspect_and_maybe_emit(_state())
    fired = _state(
        last_scenario_result={"action": "FIRE", "direction": "LONG", "m5_slot": 1},
        last_action="SCENARIO LIVE OPEN LONG 857647583068246528",
    )
    ev = d.inspect_and_maybe_emit(fired)
    assert ev is not None
    assert ev["kind"] == d.EVENT_FIRE
    assert d.inspect_and_maybe_emit(fired) is None


def test_hunt_fire_alone_is_not_a_fire_event():
    """Hunt C-FI never opens a trade, so its FIRE must not show as FIRE."""
    d.inspect_and_maybe_emit(_state())
    hunt_only = _state(
        last_hunt={"action": "FIRE", "direction": "LONG", "event": "close_through",
                   "thesis_ts": 1, "thesis_level": 100.0, "thesis_invalid": False,
                   "rearm": False, "why": "M5 close through"},
        last_action="SCENARIO WAIT (engine idle)",
    )
    ev = d.inspect_and_maybe_emit(hunt_only)
    assert ev is None or ev["kind"] != d.EVENT_FIRE


def test_failed_order_is_reported():
    d.inspect_and_maybe_emit(_state())
    failed = _state(
        last_scenario_result={"action": "FIRE", "direction": "LONG", "m5_slot": 1},
        last_action="SCENARIO LIVE ORDER FAILED",
    )
    ev = d.inspect_and_maybe_emit(failed)
    assert ev["kind"] == d.EVENT_ORDER_FAILED


def test_exit_after_fire():
    d.inspect_and_maybe_emit(_state())
    d.inspect_and_maybe_emit(_state(
        last_scenario_result={"action": "FIRE", "direction": "LONG", "m5_slot": 1},
        last_action="SCENARIO OPEN LONG FRESH_CLEAN_BREAKOUT",
    ))
    exited = _state(
        last_action="LIFECYCLE_EXIT SL",
        last_lifecycle={"action": "EXIT", "exit_kind": "SL"},
    )
    ev = d.inspect_and_maybe_emit(exited)
    assert ev["kind"] == d.EVENT_EXIT


def test_invalid_thesis():
    d.inspect_and_maybe_emit(_state())
    s = _state(last_hunt={
        "action": "WAIT", "direction": "LONG", "event": "broke",
        "thesis_ts": 1, "thesis_level": 100.0, "thesis_invalid": True,
        "rearm": False, "why": "level lost",
    })
    ev = d.inspect_and_maybe_emit(s)
    assert ev["kind"] == d.EVENT_THESIS_INVALID


def test_pullback_transition():
    d.inspect_and_maybe_emit(_state())
    s = _state(
        last_hunt={"action": "WAIT", "direction": "LONG", "event": "tap",
                   "thesis_ts": 1, "thesis_level": 100.0, "thesis_invalid": False,
                   "rearm": False, "why": "wick tap of level"},
        last_action="NO-TRADE (WAIT)",
    )
    ev = d.inspect_and_maybe_emit(s)
    assert ev["kind"] == d.EVENT_PULLBACK
