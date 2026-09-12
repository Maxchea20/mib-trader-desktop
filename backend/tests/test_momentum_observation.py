"""Step 6E — Momentum V2 AnalysisObservation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np

from src.indicators import arrays
from src.momentum import AGENT_ID, analyze, observe
from src.momentum.observe import _tv_mom0_mom1
from src.observation import AnalysisObservation, to_agent_result_compat


TF = "15m"
BAR = 900


def _bar(i, o, h, l, c, v=120.0):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _trend(n=80, drift=0.4):
    candles = []
    p = 100.0
    for i in range(n):
        p += drift + (0.15 if i % 3 == 0 else -0.05)
        candles.append(_bar(i, p - 0.2, p + 0.5, p - 0.6, p))
    return candles


def test_observation_construction():
    obs = observe(_trend(), TF)
    assert isinstance(obs, AnalysisObservation)
    assert obs.source == AGENT_ID
    assert obs.observation_type == "MOMENTUM"
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")


def test_serialization():
    import json
    d = observe(_trend(), TF).to_dict()
    json.dumps(d)
    assert d["provenance"]["source_calculation"] == "momentum.analyze"


def test_roc_measurements_present():
    obs = observe(_trend(), TF)
    for name in ("roc5", "roc12", "roc20", "acceleration_roc12", "impulse_body_atr"):
        assert obs.measurement(name) is not None, name


def test_tv_mom_optional_only():
    candles = _trend()
    obs = observe(candles, TF)
    by = {m.name: m.origin for m in obs.measurements}
    assert by["tv_mom0"] == "tradingview_luxalgo"
    assert by["tv_mom1"] == "tradingview_luxalgo"
    assert by["roc12"] == "mib"
    assert obs.flags["tv_mom_not_used_for_direction"] is True
    a = arrays(candles)
    m0, m1 = _tv_mom0_mom1(a["close"])
    assert obs.measurement("tv_mom0") == pytest.approx(m0)
    assert obs.measurement("tv_mom1") == pytest.approx(m1)
    assert np.sign(m0) == np.sign(obs.measurement("roc12")) or abs(m0) < 1e-9


def test_tv_mom0_matches_close_minus_12():
    close = arrays(_trend())["close"]
    m0, _ = _tv_mom0_mom1(close)
    assert m0 == pytest.approx(float(close[-1] - close[-13]))


def test_state_matches_analyze_evidence():
    candles = _trend()
    ar = analyze(candles, TF)
    obs = observe(candles, TF)
    state_line = [e for e in ar.evidence if e.startswith("State: ")][0]
    assert obs.state == state_line.split("State: ", 1)[1]
    assert bool(ar.valid) == obs.valid


def test_closed_candle_timestamps():
    candles = _trend()
    obs = observe(candles, TF)
    assert obs.flags["closed_candle"] is True
    assert obs.provenance.detection_timestamp == candles[-1]["ts"]
    assert obs.provenance.event_timestamp <= obs.provenance.detection_timestamp


def test_exhaustion_and_accel_flags():
    obs = observe(_trend(), TF)
    assert "accelerating" in obs.flags
    assert "exhausting" in obs.flags
    assert "expanding" in obs.flags


def test_analyze_unchanged():
    candles = _trend()
    a1 = analyze(candles, TF)
    a2 = analyze(candles, TF)
    assert a1.direction == a2.direction
    assert a1.evidence == a2.evidence
    assert a1.confidence == a2.confidence


def test_compat_unimplemented():
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(observe(_trend(), TF))
