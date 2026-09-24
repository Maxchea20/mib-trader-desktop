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
        last_hunt={"action": "FIRE", "direction": "LONG", "event": "close_through",
                   "thesis_ts": 1, "thesis_level": 100.0, "thesis_invalid": False,
                   "rearm": False, "why": "M5 close through"},
        last_action="OPEN LONG Hunt C-FI",
        last_fired_5m_ts=10,
    )
    ev = d.inspect_and_maybe_emit(fired)
    assert ev is not None
    assert ev["kind"] == d.EVENT_FIRE
    assert d.inspect_and_maybe_emit(fired) is None


def test_exit_after_fire():
    d.inspect_and_maybe_emit(_state())
    d.inspect_and_maybe_emit(_state(
        last_hunt={"action": "FIRE", "direction": "LONG", "event": "x",
                   "thesis_ts": 1, "thesis_level": 100.0, "thesis_invalid": False,
                   "rearm": False, "why": ""},
        last_action="OPEN LONG Hunt C-FI",
        last_fired_5m_ts=10,
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
