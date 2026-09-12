"""Step 6G — Volume V2 AnalysisObservation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.observation import AnalysisObservation, to_agent_result_compat
from src.volume import AGENT_ID, MIN_CANDLES, analyze, observe


TF = "15m"
BAR = 900


def _bar(i, o, h, l, c, v):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _series(n=MIN_CANDLES + 15, drift=0.25, base_vol=200.0):
    candles = []
    p = 100.0
    for i in range(n):
        p += drift
        v = base_vol * (1.2 if i % 4 == 0 else 1.0)
        candles.append(_bar(i, p - 0.15, p + 0.4, p - 0.45, p, v))
    return candles


def test_construction():
    obs = observe(_series(), TF)
    assert isinstance(obs, AnalysisObservation)
    assert obs.source == AGENT_ID
    assert obs.observation_type == "VOLUME"
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")


# ---------------------------------------------------------------------------
# "New glasses" #4 (2026-09-13): divergence is independently recomputed
# (same formula as analyze()) instead of only surfacing when it wins
# analyze()'s single-string state-priority race.
# ---------------------------------------------------------------------------

def _divergence_series():
    """Early half: strong up-move on strong volume. Recent half: price
    pushes to an even HIGHER high, but on much weaker volume -- the
    exact bearish-volume-divergence shape (price HH, volume LL)."""
    candles = []
    p = 100.0
    i = 0
    for _ in range(MIN_CANDLES - 20):
        p += 0.1
        candles.append(_bar(i, p - 0.05, p + 0.1, p - 0.15, p, 150.0)); i += 1
    for _ in range(10):
        p += 0.5
        candles.append(_bar(i, p - 0.4, p + 0.1, p - 0.5, p, 400.0)); i += 1
    for _ in range(10):
        p += 0.6
        candles.append(_bar(i, p - 0.5, p + 0.1, p - 0.6, p, 80.0)); i += 1
    return candles


def test_divergence_flag_and_tag_are_exposed():
    obs = observe(_divergence_series(), TF)
    assert obs.flags["has_divergence"] is True
    assert "VOLUME_DIVERGENCE_BEARISH" in obs.tags


def test_no_divergence_flag_when_no_divergence_present():
    obs = observe(_series(), TF)
    assert obs.flags["has_divergence"] is False
    assert not any("DIVERGENCE" in t for t in obs.tags)


def test_divergence_recomputation_matches_analyze_formula():
    """Not just presence -- the recomputed value must match what
    analyze() itself would report as `divergence` in its evidence,
    proving this isn't a different, drifted formula."""
    candles = _divergence_series()
    obs = observe(candles, TF)
    a = analyze(candles, TF)
    analyze_divergence_line = next((e for e in a.evidence if e.startswith("Divergence:")), "")
    assert "bearish" in analyze_divergence_line.lower()
    assert obs.flags["has_divergence"] is True


def test_serialization():
    import json
    d = observe(_series(), TF).to_dict()
    json.dumps(d)
    assert d["provenance"]["source_calculation"] == "volume.analyze"


def test_key_measurements():
    obs = observe(_series(), TF)
    for name in ("rvol20", "directional_bias", "impulse_body_atr", "efficiency", "displacement_atr"):
        assert obs.measurement(name) is not None, name


def test_state_matches_analyze():
    candles = _series()
    ar = analyze(candles, TF)
    obs = observe(candles, TF)
    line = [e for e in ar.evidence if e.startswith("State: ")][0]
    assert obs.state == line.split("State: ", 1)[1]
    assert bool(ar.valid) == obs.valid


def test_flags_and_tags():
    obs = observe(_series(), TF)
    assert obs.flags["closed_candle"] is True
    assert "VOLUME" in obs.tags
    assert any(t in obs.tags for t in ("VERY_LOW", "LOW", "NORMAL", "HIGH", "VERY_HIGH"))
    assert any(t in obs.tags for t in ("EXPANDING", "CONTRACTING", "STABLE"))


def test_timestamps():
    candles = _series()
    obs = observe(candles, TF)
    assert obs.provenance.detection_timestamp == candles[-1]["ts"]
    assert obs.provenance.event_timestamp == candles[-1]["ts"]
    assert obs.measurement("confirmation_lag_bars") == 0.0


def test_short_history():
    obs = observe(_series(n=10), TF)
    assert obs.valid is False
    assert obs.state == "NONE"


def test_no_future_data_and_no_book_fields():
    obs = observe(_series(), TF)
    names = {m.name for m in obs.measurements}
    assert "bid_depth" not in names
    assert "ask_depth" not in names
    assert "spread" not in names
    assert "best_bid" not in names


def test_analyze_unchanged():
    candles = _series()
    a1 = analyze(candles, TF)
    a2 = analyze(candles, TF)
    assert a1.direction == a2.direction
    assert a1.evidence == a2.evidence
    assert a1.confidence == a2.confidence


def test_compat_unimplemented():
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(observe(_series(), TF))