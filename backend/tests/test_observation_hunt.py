"""Observation hunt Brain — construction and late-entry reject."""
from src.brain.observation_hunt import evaluate_hunt, HUNT_VERSION, BAND, _vol_bad
from src.contract import STATE_WAIT, LONG
import src.brain.observation_hunt as oh
from src.observation import AnalysisEvent, AnalysisObservation, Measurement, Provenance

BAR15_TS = 1_700_000_000


def test_hunt_version():
    assert HUNT_VERSION == "OBSERVATION_HUNT_M5_V1"


def test_short_history_waits():
    out = evaluate_hunt([], {"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1})
    assert out["action"] == "WAIT"
    assert out["state"] == STATE_WAIT
    assert out["entry_readiness"] is False


def test_band_constant():
    assert BAND == 0.25


def _flat_candles_15m(n=60, price=100.0):
    return [{"ts": BAR15_TS - (n - 1 - i) * 900, "open": price, "high": price + 1,
              "low": price - 1, "close": price, "volume": 10.0} for i in range(n)]


def _empty_obs(source, obs_type, flags=None, history=None, valid=True, measurements=None, tags=None, state="NONE"):
    return AnalysisObservation(
        source=source, observation_type=obs_type, timeframe="15m",
        provenance=Provenance(source_module=source, timeframe="15m", detection_timestamp=BAR15_TS),
        state=state, flags=flags or {}, history=history or [],
        valid=valid, measurements=measurements or [], tags=tags or [],
    )


def _bos_event(direction="BULLISH"):
    return AnalysisEvent(event_type="BOS", direction=direction, timestamp=BAR15_TS, price=100.0)


def _breakout_event(direction="BULLISH"):
    return AnalysisEvent(event_type="BREAKOUT_DETECTED", direction=direction, timestamp=BAR15_TS, price=100.0)


def _patch_watchers(monkeypatch, structure_obs=None, breakout_obs=None, volume_obs=None):
    monkeypatch.setattr(oh, "obs_breakout", lambda c, tf: breakout_obs or _empty_obs("breakout", "RANGE"))
    monkeypatch.setattr(oh, "obs_structure", lambda c, tf: structure_obs or _empty_obs("market_structure", "STRUCTURE"))
    monkeypatch.setattr(oh, "obs_fvg", lambda c, tf: _empty_obs("fair_value_gap", "FVG"))
    monkeypatch.setattr(oh, "obs_sr", lambda c, tf: _empty_obs("support_resistance", "SR_LEVEL"))
    monkeypatch.setattr(oh, "obs_vol", lambda c, tf: volume_obs or _empty_obs("volume", "VOLUME_READING"))


def test_extended_bos_alone_is_skipped_not_armed(monkeypatch):
    structure_obs = _empty_obs(
        "market_structure", "STRUCTURE",
        flags={"extended_bos": True, "first_bos_after_choch": False},
        history=[_bos_event("BULLISH")],
    )
    _patch_watchers(monkeypatch, structure_obs)
    out = evaluate_hunt(_flat_candles_15m(), {"ts": 1, "open": 100, "high": 100, "low": 100, "close": 100}, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert "extended" in out["why_state"][0].lower()


def test_fresh_bos_still_arms_and_can_fire(monkeypatch):
    structure_obs = _empty_obs(
        "market_structure", "STRUCTURE",
        flags={"extended_bos": False, "first_bos_after_choch": True},
        history=[_bos_event("BULLISH")],
    )
    _patch_watchers(monkeypatch, structure_obs)
    candle_5m = {"ts": 2, "open": 100.0, "high": 100.5, "low": 100.0, "close": 100.0}
    out = evaluate_hunt(_flat_candles_15m(), candle_5m, atr_15m=100.0)
    assert out["action"] == "FIRE"
    assert out["direction"] == LONG


def test_choch_still_arms_even_if_a_later_extended_bos_would_not(monkeypatch):
    choch_event = AnalysisEvent(event_type="CHoCH", direction="BEARISH", timestamp=BAR15_TS, price=100.0)
    structure_obs = _empty_obs(
        "market_structure", "STRUCTURE",
        flags={"extended_bos": False, "first_bos_after_choch": False},
        history=[choch_event],
    )
    _patch_watchers(monkeypatch, structure_obs)
    candle_5m = {"ts": 2, "open": 100.0, "high": 100.0, "low": 99.5, "close": 100.0}
    out = evaluate_hunt(_flat_candles_15m(), candle_5m, atr_15m=100.0)
    assert out["action"] == "FIRE"
    assert out["direction"] == "SHORT"


def test_original_breakout_still_arms_even_if_low_reliability(monkeypatch):
    breakout_obs = _empty_obs(
        "breakout", "BREAKOUT", history=[_breakout_event("BULLISH")],
        valid=False,
        measurements=[Measurement("penetration_atr", 0.1), Measurement("close_location", 0.2)],
    )
    _patch_watchers(monkeypatch, breakout_obs=breakout_obs)
    candle_5m = {"ts": 2, "open": 100.0, "high": 100.5, "low": 100.0, "close": 100.0}
    out = evaluate_hunt(_flat_candles_15m(), candle_5m, atr_15m=100.0)
    assert out["action"] == "FIRE"
    assert out["direction"] == LONG


def test_vol_bad_catches_state_that_matches_directly():
    obs = _empty_obs("volume", "VOLUME", state="BEARISH_ABSORPTION", tags=["VOLUME", "BEARISH_ABSORPTION"])
    assert _vol_bad(obs, LONG) is True
    assert _vol_bad(obs, SHORT) is False


def test_vol_bad_catches_a_tag_shadowed_by_a_different_winning_state():
    obs = _empty_obs(
        "volume", "VOLUME",
        state="NEUTRAL",
        tags=["VOLUME", "NEUTRAL", "VOLUME_DIVERGENCE_BEARISH"],
    )
    assert _vol_bad(obs, LONG) is True
    assert _vol_bad(obs, SHORT) is False


def test_vol_bad_false_when_nothing_contradicts():
    obs = _empty_obs("volume", "VOLUME", state="NEUTRAL", tags=["VOLUME", "NEUTRAL", "STABLE"])
    assert _vol_bad(obs, LONG) is False
    assert _vol_bad(obs, SHORT) is False


def test_hunt_blocks_long_on_shadowed_divergence(monkeypatch):
    structure_obs = _empty_obs(
        "market_structure", "STRUCTURE",
        flags={"extended_bos": False, "first_bos_after_choch": True},
        history=[_bos_event("BULLISH")],
    )
    volume_obs = _empty_obs(
        "volume", "VOLUME",
        state="BULLISH_ABSORPTION",
        tags=["VOLUME", "BULLISH_ABSORPTION", "VOLUME_DIVERGENCE_BEARISH"],
    )
    _patch_watchers(monkeypatch, structure_obs=structure_obs, volume_obs=volume_obs)
    candle_5m = {"ts": 2, "open": 100.0, "high": 100.5, "low": 100.0, "close": 100.0}
    out = evaluate_hunt(_flat_candles_15m(), candle_5m, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert "volume contradicts" in out["why_state"][0]
