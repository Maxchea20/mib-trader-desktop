"""Contract-level tests for the Step 5 design artifact
(src/observation.py::AnalysisObservation and friends).

These test the CONTRACT ONLY -- no agent, Brain module, or
analysis_service.py imports this file's subject anywhere yet (Step 6
is the migration; this is still the design step). Purpose: prove the
proposed schema actually holds the kind of data the spec asked for
(measurable facts, first-class provenance, event history, JSON
serialization) without inventing an implicit shape nobody has
verified compiles and round-trips correctly.

Run with: python3 -m pytest tests/test_analysis_observation_contract.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.observation import (
    AnalysisEvent,
    AnalysisObservation,
    Measurement,
    ObservationLevel,
    Provenance,
    to_agent_result_compat,
)


def _breakout_observation() -> AnalysisObservation:
    """Builds the exact GOOD example from the Step 5 spec as a real
    AnalysisObservation, to prove the schema can hold it without
    reaching for a LONG/SHORT/confidence field anywhere."""
    return AnalysisObservation(
        source="breakout",
        observation_type="BREAKOUT",
        timeframe="15m",
        provenance=Provenance(
            source_module="breakout",
            timeframe="15m",
            detection_timestamp=1_700_001_000,
            event_timestamp=1_700_000_100,
            candle_timestamp=1_700_000_100,
            source_calculation="breakout._find_active_breakout",
            price_at_detection=102680.0,
        ),
        state="CONFIRMED",
        measurements=[
            Measurement("penetration", 180.0, unit="price"),
            Measurement("penetration_atr", 0.42, unit="ATR"),
            Measurement("body_ratio", 0.71, unit="ratio"),
            Measurement("wick_ratio", 0.18, unit="ratio"),
            Measurement("volume_ratio", 1.84, unit="ratio"),
        ],
        levels=[
            ObservationLevel(label="Breakout Level", price=102500.0,
                              level_type="resistance", role="origin", timeframe="15m"),
        ],
        flags={"follow_through": True, "retest": False, "failure": False},
        tags=["BULLISH"],
        notes=["Closed 180 above the broken level with strong body and volume confirmation."],
    )


def test_observation_holds_measurable_facts_not_a_directional_vote():
    obs = _breakout_observation()
    # the spec's core requirement: no direction/confidence/strength
    # vote fields exist on the dataclass at all.
    assert not hasattr(obs, "direction")
    assert not hasattr(obs, "confidence")
    assert not hasattr(obs, "strength")
    # the facts it SHOULD hold are present and typed
    assert obs.measurement("penetration_atr") == 0.42
    assert obs.measurement("volume_ratio") == 1.84
    assert obs.measurement("nonexistent", default=-1) == -1
    assert obs.flags["follow_through"] is True
    assert obs.flags["failure"] is False


def test_provenance_distinguishes_event_time_from_detection_time():
    obs = _breakout_observation()
    p = obs.provenance
    # event_timestamp (when it happened) and detection_timestamp
    # (when we noticed) are independently settable and, in this
    # fixture, deliberately different -- proving the contract can
    # express the gap Step 2 could not.
    assert p.event_timestamp == 1_700_000_100
    assert p.detection_timestamp == 1_700_001_000
    assert p.event_timestamp != p.detection_timestamp
    assert p.source_module == "breakout"
    assert p.source_calculation == "breakout._find_active_breakout"


def test_provenance_event_timestamp_is_optional_not_faked():
    """A module that genuinely cannot determine the true origin candle
    must be able to omit event_timestamp honestly, rather than the
    contract forcing a fabricated value (this is precisely the Step 2
    limitation the design doc calls out)."""
    p = Provenance(source_module="momentum", timeframe="15m", detection_timestamp=123)
    assert p.event_timestamp is None
    assert p.candle_timestamp is None
    # still fully constructible and serializable without them
    obs = AnalysisObservation(
        source="momentum", observation_type="MOMENTUM_READING",
        timeframe="15m", provenance=p,
    )
    d = obs.to_dict()
    assert d["provenance"]["event_timestamp"] is None


def test_level_by_role_replaces_keyword_sniffing():
    obs = _breakout_observation()
    origin = obs.level_by_role("origin")
    assert origin is not None
    assert origin.label == "Breakout Level"
    assert origin.price == 102500.0
    assert obs.level_by_role("reference") is None  # none tagged that role in this fixture


def test_event_history_generalizes_structure_event_shape():
    """AnalysisEvent must be able to hold the same fields
    market_state/models.py::StructureEvent already uses, so
    Structure's existing event list could eventually be represented
    here without a schema break."""
    history = [
        AnalysisEvent(event_type="CHoCH", direction="BEARISH", timestamp=1_699_999_000,
                      price=103200.0, reference_price=103400.0, swing_index=41,
                      distance_atr=0.3, source="market_structure"),
        AnalysisEvent(event_type="BOS", direction="BEARISH", timestamp=1_700_000_100,
                      price=102500.0, reference_price=103200.0, swing_index=45,
                      distance_atr=0.55, source="market_structure"),
    ]
    obs = AnalysisObservation(
        source="market_structure", observation_type="BOS", timeframe="15m",
        provenance=Provenance(source_module="market_structure", timeframe="15m",
                              detection_timestamp=1_700_000_200,
                              event_timestamp=1_700_000_100),
        history=history,
    )
    assert len(obs.history) == 2
    assert obs.history[0].event_type == "CHoCH"
    assert obs.history[1].event_type == "BOS"
    assert obs.history[1].direction == "BEARISH"


def test_direction_vocabulary_is_bullish_bearish_not_long_short():
    """Events intentionally use BULLISH/BEARISH/NEUTRAL, not
    LONG/SHORT -- a structural fact predates any trade decision Brain
    might make from it."""
    ev = AnalysisEvent(event_type="EQH", direction="BEARISH", timestamp=1, price=100.0)
    assert ev.direction in ("BULLISH", "BEARISH", "NEUTRAL")


def test_to_dict_is_json_safe_and_round_trips_structure():
    obs = _breakout_observation()
    d = obs.to_dict()
    assert d["source"] == "breakout"
    assert d["observation_type"] == "BREAKOUT"
    assert d["state"] == "CONFIRMED"
    assert isinstance(d["measurements"], list)
    assert d["measurements"][1]["name"] == "penetration_atr"
    assert d["measurements"][1]["value"] == 0.42
    assert d["levels"][0]["role"] == "origin"
    assert d["flags"]["follow_through"] is True
    # plain dict/list/str/float/bool/None only -- no dataclass objects
    # left un-converted, which would break JSON serialization exactly
    # like an un-native numpy type would (see analysis_service.py's
    # own _native() for why this matters in this codebase).
    import json
    json.dumps(d)  # must not raise


def test_required_fields_enforced_by_dataclass():
    with pytest.raises(TypeError):
        AnalysisObservation()  # source/observation_type/timeframe/provenance are required


def test_migration_adapter_is_explicitly_unimplemented_not_silently_wrong():
    """Step 5 must not ship a guessed-at direction-interpretation
    adapter. Calling it should fail loudly, not return a plausible-
    looking but unvalidated AgentResult."""
    obs = _breakout_observation()
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(obs)


# ---------------------------------------------------------------------------
# Step 5 revision (2026-09-12): tests added after reading the actual
# Support_Resistance.pine and Smart_Money.pine source, proving the
# contract amendments (ObservationLevel.upper/lower,
# AnalysisEvent.detection_timestamp/prior_state, Measurement.origin)
# actually hold the real TradingView-sourced data, not a hypothetical.
# ---------------------------------------------------------------------------

def test_observation_level_represents_a_two_sided_zone_like_an_order_block():
    """Source: Smart_Money.pine storeOrdeBlock() stores barHigh/barLow
    as a pair (the orderBlock UDT), not a single price. The original
    Step 5 ObservationLevel (price-only) could not hold this."""
    ob = ObservationLevel(
        label="Bullish Order Block", price=101850.0, level_type="order_block",
        role="reference", upper=101950.0, lower=101850.0,
    )
    assert ob.upper == 101950.0
    assert ob.lower == 101850.0
    # single-price levels remain valid and unaffected (backward compatible)
    swing = ObservationLevel(label="Swing High", price=102500.0, level_type="resistance")
    assert swing.upper is None and swing.lower is None


def test_observation_level_represents_a_fair_value_gap_zone():
    """Source: Smart_Money.pine's fairValueGap UDT stores top/bottom.
    Mirrors the same zone shape as an order block via the same
    upper/lower fields -- one mechanism for both, not two."""
    fvg = ObservationLevel(
        label="Bullish FVG", price=101500.0, level_type="fvg_upper",
        role="reference", upper=101600.0, lower=101500.0,
    )
    assert fvg.upper - fvg.lower == pytest.approx(100.0)


def test_analysis_event_prior_state_distinguishes_bos_from_choch():
    """Source: Smart_Money.pine displayStructure() --
    `tag = t_rend.bias == BEARISH ? CHOCH : BOS` for a bullish break.
    The event's own direction alone (BULLISH) is not enough to tell
    BOS from CHoCH after the fact -- prior_state is what the Pine
    source actually keys the classification on."""
    choch = AnalysisEvent(
        event_type="CHoCH", direction="BULLISH", timestamp=1_700_000_000,
        price=102680.0, reference_price=102500.0, prior_state="BEARISH",
    )
    bos = AnalysisEvent(
        event_type="BOS", direction="BULLISH", timestamp=1_700_000_900,
        price=103200.0, reference_price=103000.0, prior_state="BULLISH",
    )
    # same direction, different classification, reconstructable purely
    # from stored fields without re-running any detection logic
    assert choch.direction == bos.direction == "BULLISH"
    assert choch.prior_state != choch.direction  # trend flipped -> CHoCH
    assert bos.prior_state == bos.direction       # trend continued -> BOS


def test_analysis_event_detection_timestamp_can_lag_event_timestamp_per_leg_size():
    """Source: Smart_Money.pine leg()/getCurrentStructure() confirm a
    pivot exactly `size` bars after it occurred -- size=50 (swing),
    size=5 (internal), size=3 (EQH/EQL) are three different lags in
    the SAME script. A bundled history list needs per-event detection
    timing, which a single parent Provenance cannot provide."""
    swing_event = AnalysisEvent(
        event_type="SWING_HIGH", direction="BEARISH", timestamp=1_700_000_000,
        price=104000.0, detection_timestamp=1_700_000_000 + 50 * 900,  # 50 bars later, 15m candles
    )
    internal_event = AnalysisEvent(
        event_type="SWING_HIGH", direction="BEARISH", timestamp=1_700_003_600,
        price=103800.0, detection_timestamp=1_700_003_600 + 5 * 900,  # 5 bars later
    )
    assert swing_event.detection_timestamp - swing_event.timestamp == 50 * 900
    assert internal_event.detection_timestamp - internal_event.timestamp == 5 * 900
    # confirms the two lags are independently representable in the same history list
    history = [swing_event, internal_event]
    assert history[0].detection_timestamp != history[1].detection_timestamp - history[1].timestamp + history[0].timestamp


def test_measurement_origin_distinguishes_tradingview_from_mib_native():
    """Source: MiB's own FVG module already computes gap_atr/size_pct,
    which Support_Resistance.pine/Smart_Money.pine do not produce --
    while a threshold measurement mirroring the Pine source's adaptive
    cumulative-average formula IS TradingView-derived. Both must be
    representable, distinctly, on the same observation."""
    obs = AnalysisObservation(
        source="fair_value_gap", observation_type="FVG", timeframe="15m",
        provenance=Provenance(source_module="fair_value_gap", timeframe="15m",
                              detection_timestamp=1),
        measurements=[
            Measurement("threshold_pct", 0.021, unit="pct", origin="tradingview_luxalgo"),
            Measurement("gap_atr", 0.55, unit="ATR", origin="mib"),
        ],
    )
    by_origin = {m.name: m.origin for m in obs.measurements}
    assert by_origin["threshold_pct"] == "tradingview_luxalgo"
    assert by_origin["gap_atr"] == "mib"
    # default stays "mib" for every measurement that doesn't set it --
    # backward compatible with every Measurement built before this
    # revision, including the original _breakout_observation() fixture.
    default_measurement = Measurement("penetration_atr", 0.42, unit="ATR")
    assert default_measurement.origin == "mib"


def test_pine_equal_highs_lows_tolerance_is_representable_not_a_boolean():
    """Source: Smart_Money.pine getCurrentStructure() --
    `math.abs(p_ivot.currentLevel - low[size]) < equalHighsLowsThresholdInput * atrMeasure`.
    EQH/EQL is a tolerance-band comparison between two specific price
    levels, not an opaque true/false -- both compared prices and the
    threshold itself must be recoverable from the observation."""
    obs = AnalysisObservation(
        source="market_structure", observation_type="EQL", timeframe="15m",
        provenance=Provenance(source_module="market_structure", timeframe="15m",
                              detection_timestamp=1_700_000_000, event_timestamp=1_700_000_000),
        state="CONFIRMED",
        levels=[
            ObservationLevel(label="EQL Pivot A", price=101200.0, level_type="liquidity_pool", role="reference"),
            ObservationLevel(label="EQL Pivot B", price=101215.0, level_type="liquidity_pool", role="reference"),
        ],
        measurements=[
            Measurement("equal_low_threshold_atr_fraction", 0.1, unit="ratio", origin="tradingview_luxalgo"),
            Measurement("atr_at_detection", 150.0, unit="price", origin="mib"),
            Measurement("price_diff", 9.0, unit="price", origin="mib"),
        ],
    )
    threshold_fraction = obs.measurement("equal_low_threshold_atr_fraction")
    atr = obs.measurement("atr_at_detection")
    diff = obs.measurement("price_diff")
    assert diff < threshold_fraction * atr  # reproduces the Pine condition exactly
    assert len(obs.levels) == 2  # both compared pivots preserved, not collapsed to one flag