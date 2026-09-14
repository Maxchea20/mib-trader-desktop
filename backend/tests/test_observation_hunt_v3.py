"""Hunt V3 — first three 5m inside a live 15m."""
from src.brain.observation_hunt_v3 import evaluate_hunt_v3, HUNT_VERSION_V3
from src.contract import LONG, SHORT, STATE_WAIT

BASE = 1_700_000_000


def _15(n=30, px=100.0):
    rows = []
    for i in range(n):
        ts = BASE - (n - i) * 900
        rows.append({"ts": ts, "open": px, "high": px + 1.0, "low": px - 1.0, "close": px, "volume": 10.0})
    rows[-1]["high"] = 101.0
    rows[-1]["low"] = 99.0
    rows[-1]["close"] = 100.5
    return rows


def test_version():
    assert HUNT_VERSION_V3 == "OBSERVATION_HUNT_M5_V3"


def test_5m1_close_through_high_fires_long():
    live = [{"ts": BASE, "open": 100.8, "high": 102.5, "low": 100.7, "close": 102.2, "volume": 10}]
    out = evaluate_hunt_v3(_15(), live)
    assert out["action"] == "FIRE"
    assert out["direction"] == LONG
    assert out["hunt"]["slot"] == 1
    assert out["hunt"]["m5_path"] == "impulse_1"


def test_5m1_close_through_low_fires_short():
    live = [{"ts": BASE, "open": 99.2, "high": 99.3, "low": 97.5, "close": 97.8, "volume": 10}]
    out = evaluate_hunt_v3(_15(), live)
    assert out["action"] == "FIRE"
    assert out["direction"] == SHORT
    assert out["hunt"]["slot"] == 1


def test_5m1_inside_range_waits():
    live = [{"ts": BASE, "open": 100.0, "high": 100.4, "low": 99.7, "close": 100.2, "volume": 10}]
    out = evaluate_hunt_v3(_15(), live)
    assert out["action"] == "WAIT"
    assert out["state"] == STATE_WAIT


def test_5m1_wick_only_waits_for_next():
    live = [{"ts": BASE, "open": 100.5, "high": 101.8, "low": 100.2, "close": 100.6, "volume": 10}]
    out = evaluate_hunt_v3(_15(), live)
    assert out["action"] == "WAIT"
    assert out["hunt"]["armed"] is True


def test_5m2_hold_after_wick_fires():
    live = [
        {"ts": BASE, "open": 100.5, "high": 101.8, "low": 100.2, "close": 100.6, "volume": 10},
        {"ts": BASE + 300, "open": 100.6, "high": 101.4, "low": 100.5, "close": 101.2, "volume": 10},
    ]
    out = evaluate_hunt_v3(_15(), live)
    assert out["action"] == "FIRE"
    assert out["direction"] == LONG
    assert out["hunt"]["slot"] == 2
