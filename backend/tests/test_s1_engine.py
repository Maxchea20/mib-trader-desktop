"""S1/S2 live engine — no Hunt parent."""
from pathlib import Path
from src.brain.s1_engine import evaluate_s1, S1_VERSION, reset_s1_state


def test_s1_version():
    assert S1_VERSION == "S1_S2_C"


def test_engine_has_no_hunt_import():
    text = Path("src/brain/s1_engine.py").read_text(encoding="utf-8")
    assert "observation_hunt" not in text
    assert "evaluate_hunt" not in text
    assert "HUNT_VERSION" not in text


def test_evaluate_s1_waits_without_candles():
    reset_s1_state()
    out = evaluate_s1([], None)
    assert out["action"] == "WAIT"
    assert out["brain_version"] == S1_VERSION
