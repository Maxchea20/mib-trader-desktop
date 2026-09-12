"""Step 6C — Structure / SMC AnalysisObservation tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.indicators import find_pivots, arrays
from src.observation import AnalysisObservation, to_agent_result_compat
from src.structure import AGENT_ID, analyze, observe
from src.structure.observe import TV_EQ_SIZE, TV_EQ_THRESHOLD, TV_INTERNAL_SIZE
from src.market_state.builder import build_market_state


TF = "15m"
BAR = 900
PW = 3


def _bar(i, o, h, l, c, v=100.0):
    return {"ts": 1_700_000_000 + i * BAR, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _uptrend():
    """Rising swings so HH/HL and likely bullish BOS appear."""
    candles = []
    i = 0
    base = 100.0
    for wave in range(10):
        lo = base - 2.0
        for k in range(4):
            candles.append(_bar(i, base, base + 0.8, lo + 0.4, base + 0.3)); i += 1
        peak = base + 6.0 + wave
        candles.append(_bar(i, base + 1.0, peak, base + 0.5, peak - 0.4)); i += 1
        for k in range(3):
            candles.append(_bar(i, peak - 1.0, peak - 0.2, base - 0.5 + wave * 0.4, base + 1.0)); i += 1
        trough = base - 1.0 + wave * 0.8
        candles.append(_bar(i, base, base + 0.5, trough, trough + 0.6)); i += 1
        base += 3.0
    return candles


def test_observation_construction():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    assert isinstance(obs, AnalysisObservation)
    assert obs.source == AGENT_ID
    assert obs.observation_type == "STRUCTURE"
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")


def test_observation_serializes():
    import json
    d = observe(_uptrend(), TF, pivot_window_override=PW).to_dict()
    json.dumps(d)
    assert d["source"] == "market_structure"


def test_swing_flags_hh_hl():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    assert obs.flags["hh"] or obs.flags["hl"] or obs.flags["lh"] or obs.flags["ll"]


def test_hh_hl_lh_ll_events_when_flagged():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    types = {e.event_type for e in obs.history}
    if obs.flags["hh"]:
        assert "HH" in types
    if obs.flags["hl"]:
        assert "HL" in types
    if obs.flags["lh"]:
        assert "LH" in types
    if obs.flags["ll"]:
        assert "LL" in types


def test_bos_choch_prior_state():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    breaks = [e for e in obs.history if e.event_type in ("BOS", "CHoCH")]
    for e in breaks:
        assert e.prior_state in ("NEUTRAL", "BULLISH", "BEARISH")
        assert e.timestamp
        assert e.detection_timestamp
        if e.event_type == "CHoCH":
            assert e.prior_state in ("BULLISH", "BEARISH")


def test_event_vs_detection_timestamp():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    p = obs.provenance
    assert p.detection_timestamp == _uptrend()[-1]["ts"]
    if p.event_timestamp is not None:
        assert p.event_timestamp <= p.detection_timestamp


def test_confirmation_lag_measurement():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    assert obs.measurement("confirmation_lag_bars") == float(PW)
    assert obs.measurement("pivot_window") == float(PW)


def test_closed_candle_no_lookahead():
    candles = _uptrend()
    obs = observe(candles, TF, pivot_window_override=PW)
    assert obs.flags["closed_candle"] is True
    a = arrays(candles)
    piv = find_pivots(a["high"], a["low"], left=PW, right=PW)
    n = len(a["close"])
    for p in piv:
        assert p["i"] <= n - 1 - PW


def test_internal_structure_tag_and_level():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    assert "SWING" in obs.tags
    assert "INTERNAL" in obs.tags
    assert obs.measurement("tv_internal_leg_size") == float(TV_INTERNAL_SIZE)
    labels = {lv.label for lv in obs.levels}
    assert "Internal High" in labels or "Internal Low" in labels


def test_eq_formula_and_origin():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    assert obs.measurement("tv_eq_threshold") == TV_EQ_THRESHOLD
    assert obs.measurement("tv_eq_leg_size") == float(TV_EQ_SIZE)
    by = {m.name: m.origin for m in obs.measurements}
    assert by["tv_eq_threshold"] == "tradingview_luxalgo"
    assert by["confirmation_lag_bars"] == "mib"
    assert by["break_distance_atr"] == "mib"


def test_order_block_zone_bounds():
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    obs_ob = [lv for lv in obs.levels if lv.level_type == "order_block"]
    if obs_ob:
        lv = obs_ob[0]
        assert lv.upper is not None and lv.lower is not None
        assert lv.upper >= lv.lower
        assert "ORDER_BLOCK" in obs.tags


def test_provenance():
    candles = _uptrend()
    obs = observe(candles, TF, pivot_window_override=PW)
    assert obs.provenance.source_module == AGENT_ID
    assert obs.provenance.source_calculation == "market_state.builder.build_market_state"


def test_analyze_unchanged():
    candles = _uptrend()
    a1 = analyze(candles, TF, pivot_window_override=PW)
    a2 = analyze(candles, TF, pivot_window_override=PW)
    obs = observe(candles, TF, pivot_window_override=PW)
    assert a1.direction == a2.direction
    assert a1.evidence == a2.evidence
    assert a1.confidence == a2.confidence
    assert hasattr(a1, "confidence")
    assert not hasattr(obs, "confidence")
    st = build_market_state(candles, "UNKNOWN", TF, pivot_window_override=PW)
    assert a1.direction == st.structure.direction


def test_compat_still_unimplemented():
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(observe(_uptrend(), TF, pivot_window_override=PW))


def _downtrend(i0, base0, waves):
    """Mirror-image of _uptrend(), starting at candle index i0/price
    base0, producing `waves` bearish continuation waves. Used to build
    an uptrend -> CHoCH -> bearish-continuation sequence for testing
    BOS streak/quality tagging."""
    candles = []
    i = i0
    base = base0
    for wave in range(waves):
        hi = base + 2.0
        for k in range(4):
            candles.append(_bar(i, base, hi - 0.4, base - 0.8, base - 0.3)); i += 1
        trough = base - 6.0 - wave
        candles.append(_bar(i, base - 1.0, base - 0.5, trough, trough + 0.4)); i += 1
        for k in range(3):
            candles.append(_bar(i, trough + 1.0, hi - wave * 0.4 - 0.5, trough + 0.2, base - 1.0)); i += 1
        peak = base + 1.0 - wave * 0.8
        candles.append(_bar(i, base, peak, base - 0.5, peak - 0.6)); i += 1
        base -= 3.0
    return candles


def _reversal(waves):
    up = _uptrend()
    return up + _downtrend(len(up), up[-1]["close"], waves)


def test_extended_continuation_uptrend_has_high_bos_streak():
    """_uptrend() is 9 consecutive same-direction BOS with no CHoCH at
    all -- the canonical 'stale continuation, deep in trend' case from
    the user's own backtest notes ('BOS is not automatically a
    reversal')."""
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    assert obs.state == "BOS"
    assert obs.measurement("bos_streak") >= 3
    assert obs.flags["extended_bos"] is True
    assert obs.flags["first_bos_after_choch"] is False
    assert "EXTENDED_CONTINUATION" in obs.tags
    assert "FRESH_REVERSAL_CONFIRMATION" not in obs.tags


def test_first_bos_after_choch_is_tagged_fresh():
    """uptrend -> CHoCH (reversal) -> exactly one bearish BOS. That one
    BOS should be tagged as a fresh reversal confirmation, not lumped
    in with an extended continuation."""
    obs = observe(_reversal(3), TF, pivot_window_override=PW)
    breaks = [e.event_type for e in obs.history if e.event_type in ("BOS", "CHoCH")]
    assert breaks[-2:] == ["CHoCH", "BOS"]
    assert obs.state == "BOS"
    assert obs.measurement("bos_streak") == 1.0
    assert obs.flags["first_bos_after_choch"] is True
    assert obs.flags["extended_bos"] is False
    assert "FRESH_REVERSAL_CONFIRMATION" in obs.tags
    assert "EXTENDED_CONTINUATION" not in obs.tags


def test_bos_streak_resets_on_choch_then_grows_again():
    """The CHoCH itself resets the streak to 0 (it is not a BOS), and
    the streak grows again with each subsequent same-direction BOS."""
    obs_at_choch = observe(_reversal(2), TF, pivot_window_override=PW)
    assert obs_at_choch.state == "CHoCH"
    assert obs_at_choch.measurement("bos_streak") == 0.0

    obs_extended = observe(_reversal(8), TF, pivot_window_override=PW)
    assert obs_extended.state == "BOS"
    assert obs_extended.measurement("bos_streak") >= 3
    assert obs_extended.flags["extended_bos"] is True


def test_bos_streak_measurement_is_mib_native_origin():
    """This is MiB's own interpretation of the existing events list --
    not a TradingView-sourced formula -- so it must be tagged origin
    'mib', consistent with the Measurement.origin convention."""
    obs = observe(_uptrend(), TF, pivot_window_override=PW)
    by = {m.name: m.origin for m in obs.measurements}
    assert by["bos_streak"] == "mib"


def test_bos_quality_flags_do_not_touch_detect_math():
    """analyze()/AgentResult must be completely unaffected by this
    addition -- these flags are pure interpretation of the existing
    events list, never fed back into detection."""
    candles = _uptrend()
    a = analyze(candles, TF, pivot_window_override=PW)
    st = build_market_state(candles, "UNKNOWN", TF, pivot_window_override=PW)
    assert a.direction == st.structure.direction
    # the new fields exist only on the observation, never on AgentResult
    assert not hasattr(a, "bos_streak")
    assert not hasattr(a, "flags")


def test_eq_tolerance_matches_source():
    candles = _uptrend()
    a = arrays(candles)
    from src.indicators import atr
    period = min(200, len(a["close"]) - 1)
    atr200 = atr(a["high"], a["low"], a["close"], period)
    piv = find_pivots(a["high"], a["low"], left=TV_EQ_SIZE, right=TV_EQ_SIZE)
    highs = [p for p in piv if p["type"] == "H"]
    obs = observe(candles, TF, pivot_window_override=PW)
    if len(highs) >= 2 and atr200 > 0:
        expected = abs(highs[-1]["price"] - highs[-2]["price"]) < TV_EQ_THRESHOLD * atr200
        assert obs.flags["eqh"] == expected