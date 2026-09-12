"""Observation hunt Brain — construction and late-entry reject."""
from src.brain.observation_hunt import evaluate_hunt, HUNT_VERSION, BAND
from src.contract import STATE_WAIT


def test_hunt_version():
    assert HUNT_VERSION == "OBSERVATION_HUNT_M5_V1"


def test_short_history_waits():
    out = evaluate_hunt([], {"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1})
    assert out["action"] == "WAIT"
    assert out["state"] == STATE_WAIT
    assert out["entry_readiness"] is False


def test_band_constant():
    assert BAND == 0.25
