"""Step 6A — Breakout AnalysisObservation migration tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.breakout import (
    AGENT_ID,
    LOOKBACK,
    MAX_FOLLOWTHROUGH_LOOKBACK,
    _find_active_breakout,
    _range_as_of,
    _tv_volume_oscillator,
    analyze,
    observe,
)
from src.contract import LONG, SHORT, NEUTRAL
from src.indicators import arrays, ema
from src.observation import AnalysisObservation, to_agent_result_compat


TF = "15m"
BAR = 900


def _bar(i, o, h, l, c, v=100.0):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _range_then_break(direction="LONG", extra_hold=0, wick_reject=False):
    """Build >=45 bars: 20-bar range around 100, then a close beyond it."""
    candles = []
    for i in range(50):
        candles.append(_bar(i, 100.0, 101.0, 99.0, 100.0, v=100.0))
    i = 50
    if wick_reject:
        if direction == "LONG":
            candles.append(_bar(i, 100.2, 104.0, 99.8, 100.4, v=100.0))
        else:
            candles.append(_bar(i, 99.8, 100.2, 96.0, 99.6, v=100.0))
        return candles
    if direction == "LONG":
        candles.append(_bar(i, 100.5, 106.0, 100.2, 105.5, v=250.0))
    else:
        candles.append(_bar(i, 99.5, 99.8, 94.0, 94.5, v=250.0))
    for k in range(extra_hold):
        i += 1
        if direction == "LONG":
            candles.append(_bar(i, 105.5 + k, 107.0 + k, 105.0, 106.5 + k, v=180.0))
        else:
            candles.append(_bar(i, 94.5 - k, 95.0, 93.0 - k, 93.5 - k, v=180.0))
    return candles


def test_observation_construction_on_confirmed_break():
    candles = _range_then_break("LONG")
    obs = observe(candles, TF)
    assert isinstance(obs, AnalysisObservation)
    assert obs.source == AGENT_ID
    assert obs.observation_type == "BREAKOUT"
    assert obs.timeframe == TF
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")
    assert "BULLISH" in obs.tags
    origin = obs.level_by_role("origin")
    assert origin is not None
    assert origin.label == "Breakout Level"
    assert origin.role == "origin"


def test_observation_serializes():
    import json
    obs = observe(_range_then_break("LONG"), TF)
    d = obs.to_dict()
    json.dumps(d)
    assert d["source"] == "breakout"
    assert d["provenance"]["source_module"] == "breakout"
    assert d["levels"][0]["role"] == "origin"


def test_provenance_event_vs_detection():
    candles = _range_then_break("LONG", extra_hold=2)
    obs = observe(candles, TF)
    p = obs.provenance
    assert p.event_timestamp == candles[-3]["ts"]
    assert p.detection_timestamp == candles[-1]["ts"]
    assert p.candle_timestamp == p.event_timestamp
    assert p.event_timestamp < p.detection_timestamp
    assert p.source_calculation == "breakout._find_active_breakout"


def test_confirmation_lag_matches_bars_after_origin():
    fresh = observe(_range_then_break("LONG", extra_hold=0), TF)
    held = observe(_range_then_break("LONG", extra_hold=2), TF)
    assert fresh.measurement("confirmation_lag_bars") == 0.0
    assert held.measurement("confirmation_lag_bars") == 2.0
    assert held.measurement("follow_through_bars") >= 1.0
    assert held.flags["follow_through"] is True
    assert fresh.flags["follow_through"] is False


def test_breakout_reference_level_is_prior_range_extreme():
    candles = _range_then_break("LONG")
    a = arrays(candles)
    n = len(a["close"])
    found = _find_active_breakout(a["high"], a["low"], a["close"], n)
    assert found is not None
    origin_idx, direction, level = found
    rh, rl = _range_as_of(a["high"], a["low"], origin_idx)
    assert direction == LONG
    assert level == rh
    obs = observe(candles, TF)
    assert obs.measurement("breakout_level") == level
    assert obs.level_by_role("origin").price == round(level, 2)
    assert obs.level_by_role("origin").level_type == "resistance"


def test_short_breakout_evidence_direction():
    candles = _range_then_break("SHORT")
    obs = observe(candles, TF)
    assert "BEARISH" in obs.tags
    assert obs.level_by_role("origin").level_type == "support"
    assert obs.measurement("penetration_atr") > 0


def test_body_and_wick_evidence_present():
    obs = observe(_range_then_break("LONG"), TF)
    assert obs.measurement("body_ratio") is not None
    assert obs.measurement("wick_ratio") is not None
    assert abs(obs.measurement("body_ratio") + obs.measurement("wick_ratio") - 1.0) < 1e-6
    assert obs.measurement("displacement_atr") > 0


def test_volume_evidence_present():
    obs = observe(_range_then_break("LONG"), TF)
    assert obs.measurement("volume_zscore") is not None


def test_closed_candle_and_no_lookahead_on_origin_range():
    candles = _range_then_break("LONG", extra_hold=1)
    a = arrays(candles)
    n = len(a["close"])
    found = _find_active_breakout(a["high"], a["low"], a["close"], n)
    origin_idx, _, level = found
    window = a["high"][origin_idx - LOOKBACK:origin_idx]
    assert float(window.max()) == level
    obs = observe(candles, TF)
    assert obs.flags["closed_candle"] is True
    assert obs.measurement("breakout_level") == level
    assert obs.provenance.event_timestamp == int(a["ts"][origin_idx])
    assert obs.provenance.detection_timestamp == int(a["ts"][-1])


def test_future_bar_does_not_create_a_past_breakout():
    """Prefix without a close-beyond-range must not report a BREAKOUT."""
    base = _range_then_break("LONG")
    prefix = base[:-1]
    obs = observe(prefix, TF)
    assert obs.observation_type in ("RANGE", "BREAKOUT_REJECTION")
    assert obs.measurement("breakout_level") is None


def test_wick_rejection_is_not_a_confirmed_break():
    candles = _range_then_break("LONG", wick_reject=True)
    ar = analyze(candles, TF)
    obs = observe(candles, TF)
    assert ar.direction == NEUTRAL
    assert obs.observation_type == "BREAKOUT_REJECTION"
    assert obs.flags["wick_rejection"] is True
    assert obs.flags["follow_through"] is False


def test_analyze_behavior_unchanged_vs_observe_facts():
    candles = _range_then_break("LONG", extra_hold=1)
    ar = analyze(candles, TF)
    obs = observe(candles, TF)
    assert ar.agent == AGENT_ID
    assert ar.direction == LONG
    assert ar.key_levels[0]["price"] == obs.level_by_role("origin").price
    assert "Bullish break" in ar.evidence[0]
    assert ar.confidence > 0
    assert not hasattr(obs, "confidence")


def test_tv_volume_osc_marked_tradingview_and_matches_formula():
    candles = _range_then_break("LONG")
    obs = observe(candles, TF)
    m = next(x for x in obs.measurements if x.name == "tv_volume_osc")
    assert m.origin == "tradingview_luxalgo"
    a = arrays(candles)
    found = _find_active_breakout(a["high"], a["low"], a["close"], len(a["close"]))
    vol = a["volume"][: found[0] + 1]
    short = ema(vol, 5)
    long = ema(vol, 10)
    expected = 100.0 * (float(short[-1]) - float(long[-1])) / float(long[-1])
    assert m.value == pytest.approx(expected, rel=1e-6, abs=1e-6)


def test_mib_measurements_remain_mib_origin():
    obs = observe(_range_then_break("SHORT"), TF)
    native = {
        "penetration_atr", "displacement_atr", "body_ratio", "wick_ratio",
        "volume_zscore", "range_atr", "atr_expansion", "follow_through_bars",
        "confirmation_lag_bars", "breakout_level", "breakout_price",
    }
    for m in obs.measurements:
        if m.name in native:
            assert m.origin == "mib", m.name
        if m.name == "tv_volume_osc":
            assert m.origin == "tradingview_luxalgo"


def test_step5_compat_placeholder_still_unimplemented():
    obs = observe(_range_then_break("LONG"), TF)
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(obs)


def test_lookback_window_constant_unchanged():
    assert LOOKBACK == 20
    assert MAX_FOLLOWTHROUGH_LOOKBACK == 3


def test_tv_helper_is_exact_pine_osc():
    import numpy as np
    vol = np.array([10.0, 12, 11, 40, 13, 12, 11, 10, 9, 8, 7], dtype=float)
    got = _tv_volume_oscillator(vol)
    short = ema(vol, 5)
    long = ema(vol, 10)
    assert got == pytest.approx(100.0 * (float(short[-1]) - float(long[-1])) / float(long[-1]), rel=1e-6, abs=1e-6)
