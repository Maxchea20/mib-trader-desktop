"""Hunt C-FI rearm + frozen thesis. No WR tuning."""
from types import SimpleNamespace

from src.brain.observation_hunt_c_fi import (
    HUNT_VERSION_C_FI,
    active_setup,
    _attach_thesis,
)
from src.contract import LONG, SHORT


def _ev(et, direction, ts):
    return SimpleNamespace(event_type=et, direction=direction, timestamp=ts, detection_timestamp=ts)


def _candles(n=80, last_ts=80):
    rows = []
    for i in range(n):
        rows.append({"ts": i, "open": 100, "high": 101, "low": 99, "close": 100})
    rows[-1]["ts"] = last_ts
    return rows


def test_version():
    assert HUNT_VERSION_C_FI == "OBSERVATION_HUNT_M5_C_FI"


def test_active_setup_lingering_allows_old_event(monkeypatch):
    hist = [_ev("CHoCH", "BULLISH", 10)]
    monkeypatch.setattr(
        "src.brain.observation_hunt_c_fi.obs_structure",
        lambda *_a, **_k: SimpleNamespace(history=hist),
    )
    candles = _candles(last_ts=80)
    assert active_setup(candles, pivot=2, require_fresh=True) is None
    got = active_setup(candles, pivot=2, require_fresh=False)
    assert got == (LONG, "CHoCH", 10)


def test_active_setup_fresh_matches_last_bar(monkeypatch):
    hist = [_ev("BOS", "BEARISH", 80)]
    monkeypatch.setattr(
        "src.brain.observation_hunt_c_fi.obs_structure",
        lambda *_a, **_k: SimpleNamespace(history=hist),
    )
    assert active_setup(_candles(last_ts=80), pivot=5, require_fresh=True) == (SHORT, "BOS", 80)


def test_extended_bos_streak_is_not_a_setup(monkeypatch):
    hist = [
        _ev("CHoCH", "BULLISH", 1),
        _ev("BOS", "BULLISH", 2),
        _ev("BOS", "BULLISH", 3),
        _ev("BOS", "BULLISH", 80),
    ]
    monkeypatch.setattr(
        "src.brain.observation_hunt_c_fi.obs_structure",
        lambda *_a, **_k: SimpleNamespace(history=hist),
    )
    assert active_setup(_candles(last_ts=80), require_fresh=False) is None


def test_attach_thesis_freezes_parent_not_fill():
    candles = []
    for i in range(8):
        candles.append({
            "ts": i,
            "open": 100,
            "high": 110 if i == 2 else 101,
            "low": 90 if i == 4 else 99,
            "close": 100,
        })
    fire = {"entry": 105.5, "hunt": {"level": 104.0}}
    out = _attach_thesis(
        fire, side=LONG, event="CHoCH", gate="rearm_internal",
        ev_ts=4, candles_15m=candles, pivot=2, rearm=True,
    )
    assert out["thesis_ts"] == 4
    assert out["thesis_level"] == 104.0
    assert out["rearm"] is True
    assert out["thesis_invalid"] == 90
