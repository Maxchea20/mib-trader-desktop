"""Step 6F — Trend V2 AnalysisObservation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.observation import AnalysisObservation, to_agent_result_compat
from src.trend import AGENT_ID, MIN_CANDLES, analyze, observe


TF = "15m"
BAR = 900


def _bar(i, o, h, l, c):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": 100.0}


def _uptrend(n=MIN_CANDLES + 10):
    candles = []
    p = 100.0
    for i in range(n):
        p += 0.35
        candles.append(_bar(i, p - 0.2, p + 0.4, p - 0.5, p))
    return candles


def test_construction():
    obs = observe(_uptrend(), TF)
    assert isinstance(obs, AnalysisObservation)
    assert obs.source == AGENT_ID
    assert obs.observation_type == "TREND"
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")


def test_serialization():
    import json
    d = observe(_uptrend(), TF).to_dict()
    json.dumps(d)
    assert d["provenance"]["source_calculation"] == "trend.analyze"


def test_ema_and_separation_measurements():
    obs = observe(_uptrend(), TF)
    for name in (
        "ema20", "ema50", "ema100",
        "ema20_50_sep_atr", "ema50_100_sep_atr",
        "slope20_atr", "slope50_atr", "slope100_atr",
        "ribbon_width_atr", "bull_persistence",
    ):
        assert obs.measurement(name) is not None, name


def test_ema_levels():
    obs = observe(_uptrend(), TF)
    labels = {lv.label for lv in obs.levels}
    assert labels == {"EMA20", "EMA50", "EMA100"}


def test_state_matches_analyze():
    candles = _uptrend()
    ar = analyze(candles, TF)
    obs = observe(candles, TF)
    line = [e for e in ar.evidence if e.startswith("Trend state: ")][0]
    assert obs.state == line.split("Trend state: ", 1)[1]
    assert obs.state in {
        "STRONG_BULL", "BULL", "WEAK_BULL",
        "STRONG_BEAR", "BEAR", "WEAK_BEAR",
        "NEUTRAL",
    }


def test_flags_and_location_tag():
    obs = observe(_uptrend(), TF)
    assert obs.flags["closed_candle"] is True
    assert "bull_align" in obs.flags
    assert any(t in obs.tags for t in (
        "ABOVE_RIBBON", "BELOW_RIBBON", "INSIDE_RIBBON", "CROSSING_RIBBON",
    ))
    assert any(t in obs.tags for t in ("EXPANDING", "CONTRACTING", "STABLE"))


def test_timestamps():
    candles = _uptrend()
    obs = observe(candles, TF)
    assert obs.provenance.detection_timestamp == candles[-1]["ts"]
    assert obs.provenance.event_timestamp == candles[-1]["ts"]
    assert obs.measurement("confirmation_lag_bars") == 0.0


def test_insufficient_candles():
    obs = observe(_uptrend(n=20), TF)
    assert obs.valid is False
    assert obs.state == "NONE"


def test_analyze_unchanged():
    candles = _uptrend()
    a1 = analyze(candles, TF)
    a2 = analyze(candles, TF)
    assert a1.direction == a2.direction
    assert a1.evidence == a2.evidence
    assert a1.confidence == a2.confidence
    assert a1.strength == a2.strength


def test_compat_unimplemented():
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(observe(_uptrend(), TF))
