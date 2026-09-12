"""Observation hunt Brain — construction and late-entry reject."""
from src.brain.observation_hunt import evaluate_hunt, HUNT_VERSION, BAND
from src.contract import STATE_WAIT


def test_hunt_version():
    assert HUNT_VERSION == "OBSERVATION_HUNT_M5_V1"


def test_short_history_waits():
    out = evaluate_hunt([], {"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1})
    assert out["action"] == "WAIT"
    assert out["state"] == STATE_WAIT
    assert out["entry_readiness"] is False


def test_band_constant():
    assert BAND == 0.25


# ---------------------------------------------------------------------------
# "New glasses" #1 wiring (2026-09-13): extended/stale BOS no longer
# arms the hunt on its own. Structure/observe.py's detect math is
# untouched; these tests isolate the hunt-side gating logic itself by
# monkeypatching the five watcher observe() calls evaluate_hunt makes,
# so each scenario exercises exactly one variable (extended vs. fresh
# BOS) without needing to hand-construct a full multi-agent candle
# history that happens to produce the right structure event.
# ---------------------------------------------------------------------------
import src.brain.observation_hunt as oh
from src.observation import AnalysisEvent, AnalysisObservation, Provenance
from src.contract import LONG, STATE_WAIT

BAR15_TS = 1_700_000_000


def _flat_candles_15m(n=60, price=100.0):
    return [{"ts": BAR15_TS - (n - 1 - i) * 900, "open": price, "high": price + 1,
              "low": price - 1, "close": price, "volume": 10.0} for i in range(n)]


def _empty_obs(source, obs_type, flags=None, history=None):
    return AnalysisObservation(
        source=source, observation_type=obs_type, timeframe="15m",
        provenance=Provenance(source_module=source, timeframe="15m", detection_timestamp=BAR15_TS),
        state="NONE", flags=flags or {}, history=history or [],
    )


def _bos_event(direction="BULLISH"):
    return AnalysisEvent(event_type="BOS", direction=direction, timestamp=BAR15_TS, price=100.0)


def _patch_watchers(monkeypatch, structure_obs, breakout_history=None):
    monkeypatch.setattr(oh, "obs_breakout", lambda c, tf: _empty_obs("breakout", "BREAKOUT", history=breakout_history or []))
    monkeypatch.setattr(oh, "obs_structure", lambda c, tf: structure_obs)
    monkeypatch.setattr(oh, "obs_fvg", lambda c, tf: _empty_obs("fair_value_gap", "FVG"))
    monkeypatch.setattr(oh, "obs_sr", lambda c, tf: _empty_obs("support_resistance", "SR_LEVEL"))
    monkeypatch.setattr(oh, "obs_vol", lambda c, tf: _empty_obs("volume", "VOLUME_READING"))


def test_extended_bos_alone_is_skipped_not_armed(monkeypatch):
    structure_obs = _empty_obs(
        "market_structure", "STRUCTURE",
        flags={"extended_bos": True, "first_bos_after_choch": False},
        history=[_bos_event("BULLISH")],
    )
    _patch_watchers(monkeypatch, structure_obs)

    out = evaluate_hunt(_flat_candles_15m(), {"ts": 1, "open": 100, "high": 100, "low": 100, "close": 100}, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert out["state"] == STATE_WAIT
    assert "extended" in out["why_state"][0].lower()
    assert out["bos_quality"]["extended_bos"] is True


def test_fresh_bos_still_arms_and_can_fire(monkeypatch):
    structure_obs = _empty_obs(
        "market_structure", "STRUCTURE",
        flags={"extended_bos": False, "first_bos_after_choch": True},
        history=[_bos_event("BULLISH")],
    )
    _patch_watchers(monkeypatch, structure_obs)

    # candle_5m closes exactly at the fallback level (=p15, no SR/FVG
    # levels supplied) with no penetration -- should tag, hold, and FIRE.
    candle_5m = {"ts": 2, "open": 100.0, "high": 100.5, "low": 100.0, "close": 100.0}
    out = evaluate_hunt(_flat_candles_15m(), candle_5m, atr_15m=100.0)
    assert out["action"] == "FIRE"
    assert out["direction"] == LONG
    assert out["bos_quality"]["extended_bos"] is False
    assert out["bos_quality"]["first_bos_after_choch"] is True


def test_choch_still_arms_even_if_a_later_extended_bos_would_not(monkeypatch):
    """A CHoCH is never subject to the extended-BOS filter -- only
    BOS-type triggers are gated, matching the notes: 'CHoCH = first
    sign of flip. BOS is not automatically a reversal.'"""
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


def test_extended_bos_diagnostics_present_on_every_wait_branch(monkeypatch):
    """bos_quality should be attached even when the WAIT reason is
    unrelated (e.g. no trigger at all), so Grok's backtest tooling can
    log/aggregate it consistently regardless of which branch WAITed."""
    structure_obs = _empty_obs("market_structure", "STRUCTURE", flags={"extended_bos": False})
    _patch_watchers(monkeypatch, structure_obs)
    out = evaluate_hunt(_flat_candles_15m(), {"ts": 2, "open": 100, "high": 100, "low": 100, "close": 100}, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert "bos_quality" in out
    assert out["bos_quality"]["extended_bos"] is False