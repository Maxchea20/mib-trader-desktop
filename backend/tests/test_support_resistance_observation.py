"""Step 6B — Support/Resistance AnalysisObservation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.indicators import find_pivots, arrays, ema
from src.observation import AnalysisObservation, to_agent_result_compat
from src.support_resistance import (
    AGENT_ID,
    PIVOT_LEFT,
    PIVOT_RIGHT,
    analyze,
    observe,
)
from src.support_resistance.observe import TV_LEFT, TV_RIGHT, _tv_volume_oscillator


TF = "15m"
BAR = 900


def _bar(i, o, h, l, c, v=100.0):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _sr_candles():
    """~80 bars with repeated swing highs ~110 and lows ~90 so clustering fires."""
    candles = []
    i = 0
    for cycle in range(8):
        for k in range(5):
            candles.append(_bar(i, 100.0, 102.0, 98.0, 100.0)); i += 1
        candles.append(_bar(i, 101.0, 110.0, 100.5, 108.0, v=140.0)); i += 1
        for k in range(5):
            candles.append(_bar(i, 105.0, 107.0, 99.0, 101.0)); i += 1
        candles.append(_bar(i, 99.0, 100.5, 90.0, 92.0, v=140.0)); i += 1
        for k in range(4):
            candles.append(_bar(i, 94.0, 98.0, 91.0, 96.0)); i += 1
    return candles


def test_observation_construction():
    obs = observe(_sr_candles(), TF)
    assert isinstance(obs, AnalysisObservation)
    assert obs.source == AGENT_ID
    assert obs.observation_type == "SUPPORT_RESISTANCE"
    assert obs.timeframe == TF
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")


def test_observation_serializes():
    import json
    d = observe(_sr_candles(), TF).to_dict()
    json.dumps(d)
    assert d["source"] == "support_resistance"
    assert d["provenance"]["source_calculation"] == "indicators.find_pivots"


def test_support_and_resistance_levels():
    obs = observe(_sr_candles(), TF)
    types = {lv.level_type for lv in obs.levels}
    assert "support" in types or "resistance" in types
    assert obs.flags["has_support"] or obs.flags["has_resistance"]


def test_zone_upper_lower():
    obs = observe(_sr_candles(), TF)
    zoned = [lv for lv in obs.levels if lv.upper is not None and lv.lower is not None]
    assert zoned
    for lv in zoned:
        assert lv.upper >= lv.lower


def test_touch_count_present():
    obs = observe(_sr_candles(), TF)
    if obs.valid:
        assert obs.measurement("touch_count") is not None
        assert obs.measurement("touch_count") >= 1


def test_recency_present():
    obs = observe(_sr_candles(), TF)
    if obs.valid:
        assert obs.measurement("recency_bars") is not None
        assert obs.measurement("recency_score") is not None


def test_reaction_quality_present():
    obs = observe(_sr_candles(), TF)
    if obs.valid:
        assert obs.measurement("reaction_quality_atr") is not None


def test_provenance():
    candles = _sr_candles()
    obs = observe(candles, TF)
    p = obs.provenance
    assert p.source_module == AGENT_ID
    assert p.detection_timestamp == candles[-1]["ts"]
    assert p.source_calculation == "indicators.find_pivots"
    if p.event_timestamp is not None:
        assert p.event_timestamp <= p.detection_timestamp


def test_closed_candle_flag():
    obs = observe(_sr_candles(), TF)
    assert obs.flags["closed_candle"] is True


def test_no_lookahead_on_pivots():
    candles = _sr_candles()
    a = arrays(candles)
    n = len(a["close"])
    piv = find_pivots(a["high"], a["low"], left=PIVOT_LEFT, right=PIVOT_RIGHT)
    assert piv
    for p in piv:
        assert p["i"] <= n - 1 - PIVOT_RIGHT
        assert p["i"] >= PIVOT_LEFT


def test_uses_centralized_find_pivots(monkeypatch):
    import importlib
    called = {"n": 0}
    real = find_pivots

    def wrapped(high, low, left=3, right=3):
        called["n"] += 1
        return real(high, low, left=left, right=right)

    mod = importlib.import_module("src.support_resistance.observe")
    monkeypatch.setattr(mod, "find_pivots", wrapped)
    mod.observe(_sr_candles(), TF)
    assert called["n"] >= 1


def test_analyze_behavior_unchanged():
    candles = _sr_candles()
    ar = analyze(candles, TF)
    obs = observe(candles, TF)
    assert ar.agent == AGENT_ID
    assert ar.valid == obs.valid
    if ar.key_levels and obs.levels:
        assert ar.key_levels[0]["type"] in {lv.level_type for lv in obs.levels}
    assert hasattr(ar, "confidence")
    assert not hasattr(obs, "confidence")


def test_tv_measurements_marked():
    obs = observe(_sr_candles(), TF)
    by = {m.name: m.origin for m in obs.measurements}
    assert by["tv_volume_osc"] == "tradingview_luxalgo"
    assert by["tv_last_pivot_high"] == "tradingview_luxalgo"
    assert by["tv_pivot_left"] == "tradingview_luxalgo"
    assert obs.measurement("tv_pivot_left") == float(TV_LEFT)
    assert obs.measurement("tv_pivot_right") == float(TV_RIGHT)
    assert obs.measurement("tv_pivot_extra_offset_bars") == 1.0


def test_mib_measurements_marked():
    obs = observe(_sr_candles(), TF)
    native = {
        "touch_count", "recency_bars", "recency_score", "reaction_quality_atr",
        "tolerance_pct", "zone_count", "pivot_left", "pivot_right", "confirmation_lag_bars",
    }
    for m in obs.measurements:
        if m.name in native:
            assert m.origin == "mib", m.name


def test_tv_osc_matches_formula():
    candles = _sr_candles()
    a = arrays(candles)
    got = _tv_volume_oscillator(a["volume"])
    short = ema(a["volume"], 5)
    long = ema(a["volume"], 10)
    expected = 100.0 * (float(short[-1]) - float(long[-1])) / float(long[-1])
    assert got == pytest.approx(expected, rel=1e-6, abs=1e-6)


def test_tv_osc_does_not_change_analyze():
    candles = _sr_candles()
    a1 = analyze(candles, TF)
    a2 = analyze(candles, TF)
    assert a1.direction == a2.direction
    assert a1.evidence == a2.evidence


def test_compat_placeholder_still_unimplemented():
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(observe(_sr_candles(), TF))


def test_mib_pivot_window_not_tv_defaults():
    assert PIVOT_LEFT == 3
    assert PIVOT_RIGHT == 3
    assert TV_LEFT == 15
    assert TV_RIGHT == 15
