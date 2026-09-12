"""Step 6D — Fair Value Gap AnalysisObservation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.fair_value_gap import AGENT_ID, MIN_FVG_PCT, MIN_DISPLACEMENT_ATR, analyze, observe
from src.fair_value_gap.observe import _tv_adaptive_threshold
from src.indicators import arrays
from src.observation import AnalysisObservation, to_agent_result_compat


TF = "15m"
BAR = 900


def _bar(i, o, h, l, c, v=100.0):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _with_bullish_fvg():
    candles = []
    for i in range(40):
        candles.append(_bar(i, 100.0, 100.4, 99.6, 100.0))
    candles.append(_bar(40, 100.0, 100.3, 99.7, 100.1))
    candles.append(_bar(41, 100.2, 103.5, 100.1, 103.2))
    candles.append(_bar(42, 103.0, 103.8, 101.2, 103.4))
    for i in range(43, 50):
        candles.append(_bar(i, 103.2, 103.6, 102.8, 103.3))
    return candles


def _with_bearish_fvg():
    candles = []
    for i in range(40):
        candles.append(_bar(i, 100.0, 100.4, 99.6, 100.0))
    candles.append(_bar(40, 100.0, 100.4, 99.7, 99.9))
    candles.append(_bar(41, 99.8, 99.9, 96.4, 96.8))
    candles.append(_bar(42, 96.9, 98.5, 96.2, 96.6))
    for i in range(43, 50):
        candles.append(_bar(i, 96.6, 97.0, 96.2, 96.5))
    return candles


def test_bullish_fvg():
    obs = observe(_with_bullish_fvg(), TF)
    assert obs.valid
    assert obs.flags["bullish"]
    assert obs.measurement("gap_size") > 0


def test_bearish_fvg():
    obs = observe(_with_bearish_fvg(), TF)
    assert obs.valid
    assert obs.flags["bearish"]


def test_upper_lower_zone():
    obs = observe(_with_bullish_fvg(), TF)
    zoned = [lv for lv in obs.levels if lv.upper is not None]
    assert zoned
    for lv in zoned:
        assert lv.upper > lv.lower


def test_gap_size_pct_atr_displacement():
    obs = observe(_with_bullish_fvg(), TF)
    assert obs.measurement("gap_size") is not None
    assert obs.measurement("gap_pct") is not None
    assert obs.measurement("gap_atr") is not None
    assert obs.measurement("displacement_atr") is not None
    assert obs.measurement("gap_pct") >= MIN_FVG_PCT
    assert obs.measurement("displacement_atr") >= MIN_DISPLACEMENT_ATR


def test_timestamps():
    candles = _with_bullish_fvg()
    obs = observe(candles, TF)
    assert obs.provenance.detection_timestamp == candles[-1]["ts"]
    assert obs.provenance.event_timestamp is not None
    assert obs.provenance.event_timestamp <= obs.provenance.detection_timestamp


def test_closed_candle_no_lookahead():
    obs = observe(_with_bullish_fvg(), TF)
    assert obs.flags["closed_candle"] is True
    assert obs.flags["tv_lookahead_reproduced"] is False
    assert obs.measurement("tv_lookahead_security_used") == 0.0


def test_mitigation_states():
    obs = observe(_with_bullish_fvg(), TF)
    assert obs.state in ("FRESH", "PARTIAL", "FILLED", "NONE")
    assert "has_fresh" in obs.flags


def test_tv_middle_close_flag_present():
    obs = observe(_with_bullish_fvg(), TF)
    assert "tv_middle_close" in obs.flags
    by = {m.name: m.origin for m in obs.measurements}
    assert by["tv_middle_close_pass_count"] == "tradingview_luxalgo"
    assert by["min_fvg_pct"] == "mib"
    assert by["gap_atr"] == "mib"


def test_tv_adaptive_threshold_causal():
    candles = _with_bullish_fvg()
    a = arrays(candles)
    got = _tv_adaptive_threshold(a["open"], a["close"])
    acc = 0.0
    last = 0.0
    n = len(a["close"])
    for i in range(1, n):
        o = float(a["open"][i - 1])
        acc += abs((float(a["close"][i - 1]) - o) / (o * 100.0))
        last = (acc / float(i)) * 2.0
    assert got == pytest.approx(last, rel=1e-9)
    obs = observe(candles, TF)
    assert obs.measurement("tv_adaptive_threshold") == pytest.approx(got, rel=1e-4, abs=1e-8)


def test_serialization_and_contract():
    import json
    obs = observe(_with_bullish_fvg(), TF)
    json.dumps(obs.to_dict())
    assert isinstance(obs, AnalysisObservation)
    assert not hasattr(obs, "confidence")
    assert obs.source == AGENT_ID
    assert obs.observation_type == "FAIR_VALUE_GAP"


def test_analyze_unchanged():
    candles = _with_bullish_fvg()
    a1 = analyze(candles, TF)
    a2 = analyze(candles, TF)
    obs = observe(candles, TF)
    assert a1.direction == a2.direction
    assert a1.evidence == a2.evidence
    assert hasattr(a1, "confidence")
    assert not hasattr(obs, "confidence")


def test_compat_unimplemented():
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(observe(_with_bullish_fvg(), TF))
