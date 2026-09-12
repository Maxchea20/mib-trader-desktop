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
from src.observation import AnalysisEvent, AnalysisObservation, Measurement, Provenance
from src.contract import LONG, SHORT, STATE_WAIT

BAR15_TS = 1_700_000_000


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


# ---------------------------------------------------------------------------
# "New glasses" #2 wiring (2026-09-13): a low-reliability or already-
# failed breakout no longer arms the hunt on its own. Same pattern as
# #1 -- breakout/observe.py's `valid`/`flags["failure"]` already
# existed and were simply not being read by the hunt before this.
# breakout/__init__.py's detection math is untouched.
# ---------------------------------------------------------------------------

def test_low_reliability_breakout_is_skipped_not_armed(monkeypatch):
    breakout_obs = _empty_obs(
        "breakout", "BREAKOUT", history=[_breakout_event("BULLISH")],
        valid=False,  # low-reliability: thin penetration AND thin volume
        measurements=[Measurement("penetration_atr", 0.1), Measurement("close_location", 0.2)],
    )
    _patch_watchers(monkeypatch, breakout_obs=breakout_obs)

    out = evaluate_hunt(_flat_candles_15m(), {"ts": 1, "open": 100, "high": 100, "low": 100, "close": 100}, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert "low-reliability" in out["why_state"][0] or "low-reliability" in out["blocking_reasons"][0]
    assert out["breakout_quality"]["low_reliability"] is True


def test_already_failed_breakout_is_skipped_not_armed(monkeypatch):
    """Even if breakout/observe.py still reports valid=True on some
    other measure, an already-FAILED lifecycle (decisive close back
    through the level) must block arming -- this is the literal
    'fail if snap-back' case."""
    breakout_obs = _empty_obs(
        "breakout", "BREAKOUT", history=[_breakout_event("BULLISH")],
        valid=True, flags={"failure": True},
        measurements=[Measurement("penetration_atr", 0.8), Measurement("close_location", 0.9)],
    )
    _patch_watchers(monkeypatch, breakout_obs=breakout_obs)

    out = evaluate_hunt(_flat_candles_15m(), {"ts": 1, "open": 100, "high": 100, "low": 100, "close": 100}, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert out["breakout_quality"]["already_failed"] is True


def test_strong_breakout_still_arms_and_can_fire(monkeypatch):
    breakout_obs = _empty_obs(
        "breakout", "BREAKOUT", history=[_breakout_event("BEARISH")],
        valid=True, flags={"failure": False},
        measurements=[Measurement("penetration_atr", 0.9), Measurement("close_location", 0.85)],
    )
    _patch_watchers(monkeypatch, breakout_obs=breakout_obs)

    candle_5m = {"ts": 2, "open": 100.0, "high": 100.0, "low": 99.5, "close": 100.0}
    out = evaluate_hunt(_flat_candles_15m(), candle_5m, atr_15m=100.0)
    assert out["action"] == "FIRE"
    assert out["direction"] == SHORT
    assert out["breakout_quality"]["low_reliability"] is False
    assert out["breakout_quality"]["already_failed"] is False
    assert out["breakout_quality"]["penetration_atr"] == 0.9


def test_breakout_quality_present_even_with_default_watchers(monkeypatch):
    _patch_watchers(monkeypatch)
    out = evaluate_hunt(_flat_candles_15m(), {"ts": 2, "open": 100, "high": 100, "low": 100, "close": 100}, atr_15m=100.0)
    assert out["action"] == "WAIT"
    assert "breakout_quality" in out


# ---------------------------------------------------------------------------
# "New glasses" #4 wiring (2026-09-13): _vol_bad() now checks the full
# tags list, not just the single collapsed `state` string, so a
# contradiction that analyze()'s state-priority logic shadowed (e.g.
# absorption won the state string, but divergence was ALSO true) still
# blocks the trade. volume/observe.py's detect math is untouched --
# absorption/exhaustion/divergence are all still computed exactly as
# before, just no longer collapsed to one value before reaching here.
# ---------------------------------------------------------------------------
from src.brain.observation_hunt import _vol_bad


def test_vol_bad_catches_state_that_matches_directly():
    """Baseline: the original behavior (state itself is the
    contradiction) must still work."""
    obs = _empty_obs("volume", "VOLUME", state="BEARISH_ABSORPTION", tags=["VOLUME", "BEARISH_ABSORPTION"])
    assert _vol_bad(obs, LONG) is True
    assert _vol_bad(obs, SHORT) is False


def test_vol_bad_catches_a_tag_shadowed_by_a_different_winning_state():
    """The core fix: divergence is TRUE but a neutral/unrelated state
    won analyze()'s state-priority race, so `state` alone would hide
    it. Before this fix, _vol_bad only looked at `state` and would
    have returned False here, wrongly allowing a LONG through despite
    a real bearish divergence underneath."""
    obs = _empty_obs(
        "volume", "VOLUME",
        state="NEUTRAL",  # the "winning" state -- not itself contradictory for either side
        tags=["VOLUME", "NEUTRAL", "VOLUME_DIVERGENCE_BEARISH"],  # but divergence also fired underneath
    )
    assert _vol_bad(obs, LONG) is True   # caught via tags, not state
    assert _vol_bad(obs, SHORT) is False


def test_vol_bad_false_when_nothing_contradicts():
    obs = _empty_obs("volume", "VOLUME", state="NEUTRAL", tags=["VOLUME", "NEUTRAL", "STABLE"])
    assert _vol_bad(obs, LONG) is False
    assert _vol_bad(obs, SHORT) is False


def test_hunt_blocks_long_on_shadowed_divergence(monkeypatch):
    """End-to-end: a fresh bullish BOS with a volume observation whose
    STATE looks fine but whose TAGS reveal a shadowed bearish
    divergence must still WAIT, not FIRE."""
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