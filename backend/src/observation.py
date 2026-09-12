"""STEP 5 DESIGN ARTIFACT — proposed replacement for the vote-shaped
AgentResult contract (see contract.py). NOT imported or used by any
agent, Brain module, or analysis_service.py yet — this file exists to
make the Step 5 design concrete and testable, per the audit's
"deliver artifacts, not descriptions" principle. Migration (wiring
this into a real agent) is Step 6, not this step.

Design goals (see the accompanying design document for full
rationale):
  1. Represent measurable FACTS about the market, not a LONG/SHORT
     vote with a confidence score.
  2. Provenance is first-class: every observation records where it
     came from, at what candle, and when it was detected --
     distinguishing "when the event actually happened" from "when we
     noticed it," which AgentResult could never express.
  3. Typed, not a giant untyped dict -- Measurement and
     ObservationLevel are explicit dataclasses, not bare dicts with
     string keys the reader has to guess the shape of.
  4. Event history is a first-class list, generalizing the pattern
     market_state/models.py::StructureState already uses for
     StructureEvent -- not invented fresh here.
  5. Backtest/live parity: nothing in this contract can only be
     populated in one of the two contexts. `event_timestamp` is
     Optional specifically because not every source can determine it
     yet (see Provenance docstring) -- it is never faked.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@dataclass
class Provenance:
    """Where an observation came from and when -- both in market time
    and in wall-clock/pipeline time. These are deliberately different
    fields because they answer different questions:

      - event_timestamp: when the underlying market event actually
        happened (e.g. the candle where a breakout closed beyond its
        range). This is what a trader means by "when did this occur."
      - candle_timestamp: the timestamp of the candle this specific
        measurement was computed FROM. Usually equal to
        event_timestamp for a fresh trigger, but can differ for a
        measurement taken against an older, still-relevant candle
        (e.g. re-measuring an origin level's penetration on a later
        bar).
      - detection_timestamp: when THIS PROCESS computed/logged the
        observation. Always available (it's just "now" at computation
        time) -- this is what decision_log's trigger_events.timestamp
        currently records (see Step 2's documented limitation), and
        is kept here explicitly so callers can tell the difference
        between "when it happened" and "when we noticed," instead of
        silently only having the latter.

    event_timestamp/candle_timestamp are Optional because not every
    analytical module can determine the TRUE origin candle today
    (e.g. a module that only look at the latest bar, with no history
    walk-back) -- Optional here is honest about that gap rather than
    papering over it with today's tick time. A future agent migration
    that CAN determine it (most can, since they already receive
    candle history) should populate it; nothing about this contract
    forces the dishonest fallback Step 2 had to accept for AgentResult.
    """
    source_module: str                       # e.g. "breakout", "market_structure"
    timeframe: str                           # e.g. "15m"
    detection_timestamp: int                 # always available -- "when we computed this"
    event_timestamp: Optional[int] = None    # when the underlying market event happened
    candle_timestamp: Optional[int] = None   # candle this measurement was taken from
    source_calculation: Optional[str] = None  # e.g. "indicators.atr", "market_state.builder._find_pivots"
    price_at_detection: Optional[float] = None


# ---------------------------------------------------------------------------
# Measurements & levels -- typed, not a bare dict
# ---------------------------------------------------------------------------

@dataclass
class Measurement:
    """One named, typed numeric fact. Replaces stuffing arbitrary
    numbers into a dict with string keys and no documented units --
    e.g. `penetration_atr=0.42` instead of a bare
    `{"penetration_atr": 0.42}` the reader has to trust is in ATR
    units by convention alone.

    `origin` (added 2026-09-12, Step 5 revision, after reading the
    actual TradingView/LuxAlgo Pine source): distinguishes a
    measurement whose DEFINITION comes from a reference implementation
    (e.g. the LuxAlgo Smart Money Concepts or Support/Resistance
    scripts) from one MiB computes natively with no TradingView
    equivalent. This matters concretely -- e.g. MiB's own FVG module
    already computes `gap_atr`/`size_pct`, which the Pine source does
    not; conversely a `threshold_pct` measurement mirroring the Pine
    script's adaptive cumulative-average threshold should be tagged as
    TradingView-derived so a future reader doesn't assume it's an
    independently-chosen MiB constant. Optional/defaulted so this is
    purely additive to the original Step 5 design.
    """
    name: str                 # e.g. "penetration_atr", "volume_ratio", "body_ratio"
    value: float
    unit: Optional[str] = None  # e.g. "ATR", "pct", "ratio", "bars", "price" -- None for dimensionless
    origin: str = "mib"          # "mib" | "tradingview_luxalgo" -- see docstring above


@dataclass
class ObservationLevel:
    """Replaces AgentResult.key_levels' untyped
    {"label","price","type"} dicts. Adds `role`, which replaces
    scoring.py's fragile _ORIGIN_LABEL_HINTS keyword-substring
    matching (checking whether the string "BOS" or "TRIGGER" appears
    in a label) with an explicit, unambiguous field a producer sets
    directly -- see the design document's section on this.

    `upper`/`lower` (added 2026-09-12, Step 5 revision): the original
    single-`price` design could not represent a TWO-SIDED ZONE.
    Reading the actual LuxAlgo Smart Money Concepts source showed this
    is a real gap, not a hypothetical one -- an Order Block is stored
    as a `barHigh`/`barLow` pair (see `storeOrdeBlock()` in the Pine
    source) and a Fair Value Gap is a `top`/`bottom` pair (see the
    `fairValueGap` type and `drawFairValueGaps()`), neither of which
    fits into one `price` float. Both fields are Optional and default
    to None so every existing single-price level (a pivot, an S/R
    line, a neckline) is unaffected -- `price` remains the field those
    use, and remains required. A zone-shaped level should set `price`
    to whichever boundary is most relevant to reference (or the
    midpoint) AND populate `upper`/`lower` with the full zone, rather
    than forcing callers to choose between "the one price" and "the
    two boundaries."
    """
    label: str                     # e.g. "BOS", "Swing High", "Order Block High", "Neckline"
    price: float
    level_type: str                # "support" | "resistance" | "neckline" | "order_block" |
                                    # "fvg_upper" | "fvg_lower" | "liquidity_pool" | "trendline"
    role: str = "context"          # "origin" | "reference" | "context" -- explicit, not keyword-sniffed
    timeframe: Optional[str] = None
    upper: Optional[float] = None  # zone top, e.g. Order Block barHigh / FVG top
    lower: Optional[float] = None  # zone bottom, e.g. Order Block barLow / FVG bottom


# ---------------------------------------------------------------------------
# Event history -- generalizes market_state/models.py::StructureEvent
# ---------------------------------------------------------------------------

@dataclass
class AnalysisEvent:
    """A single structural/analytical event, for building the kind of
    sequence the spec describes (BULLISH STRUCTURE -> EQH -> LIQUIDITY
    SWEEP -> BEARISH DISPLACEMENT -> CHoCH -> ...). Deliberately the
    same shape as market_state/models.py::StructureEvent (event ->
    event_type, direction, price, timestamp, reference_price,
    swing_index, distance_atr) plus a `source` field, so Structure's
    existing event list can be represented here without a second,
    incompatible schema -- generalizing an existing, working pattern
    rather than inventing a new one.

    `direction` uses BULLISH/BEARISH/NEUTRAL rather than LONG/SHORT
    deliberately: LONG/SHORT are trade-execution words; an event like
    a liquidity sweep or a CHoCH is a market-structure fact that
    predates and is independent of any trade decision Brain might
    later make from it.

    `detection_timestamp` and `prior_state` (added 2026-09-12, Step 5
    revision, after reading the actual LuxAlgo Smart Money Concepts
    source):

    - `detection_timestamp`: a single shared Provenance on the parent
      AnalysisObservation cannot correctly timestamp EACH event in a
      `history` list -- a bundled history can span events confirmed
      at very different lags (the Pine source's own leg()/
      getCurrentStructure() mechanism confirms a swing pivot exactly
      `size` bars after it occurred, where `size` differs between
      swing structure (default 50), internal structure (fixed 5), and
      EQH/EQL (default 3) -- three different confirmation lags in the
      same script). `timestamp` on this dataclass is the EVENT
      timestamp (when it happened, matching StructureEvent's existing
      semantics); this new field is WHEN it became knowable. Optional
      and defaults to None so existing StructureEvent-shaped data
      (which has no equivalent field) still fits without changes.
    - `prior_state`: the actual Pine source determines BOS vs CHoCH
      by comparing the break direction against the trend bias that
      existed BEFORE this event (`tag = t_rend.bias == BEARISH ?
      CHOCH : BOS`, see `displayStructure()`). Without recording that
      prior bias, a BOS/CHoCH event's classification could never be
      independently verified or reconstructed from stored history
      alone -- exactly the gap Step 5F/5E asked to be closed. Optional
      string (e.g. "BULLISH"/"BEARISH"/"NEUTRAL"), None when not
      applicable (e.g. a plain swing-high/swing-low event that isn't
      itself a BOS/CHoCH classification).
    """
    event_type: str                      # e.g. "BOS", "CHoCH", "EQH", "EQL", "LIQUIDITY_SWEEP",
                                          # "BREAKOUT_DETECTED", "RETEST", "FAILURE"
    direction: str                       # "BULLISH" | "BEARISH" | "NEUTRAL"
    timestamp: int                       # EVENT timestamp -- candle the event occurred on
                                          # (CLOSED candle only -- see the repaint note below)
    price: float
    reference_price: Optional[float] = None
    swing_index: Optional[int] = None
    distance_atr: float = 0.0
    source: Optional[str] = None         # which module logged this event, e.g. "market_structure"
    detection_timestamp: Optional[int] = None  # WHEN this became knowable (may lag `timestamp`)
    prior_state: Optional[str] = None          # trend/bias immediately before this event --
                                                # what distinguishes e.g. CHoCH from BOS


# ---------------------------------------------------------------------------
# The core contract
# ---------------------------------------------------------------------------

@dataclass
class AnalysisObservation:
    """Proposed replacement for AgentResult. Represents a MEASURED FACT
    about current or recent market conditions, not a directional vote.

    Compare to the spec's own BAD/GOOD example:
      BAD:  direction=LONG, confidence=82, strength=4
      GOOD: level=102500, break_direction=BULLISH, penetration_atr=0.42,
            body_ratio=0.71, volume_ratio=1.84, follow_through=true

    This dataclass is shaped to hold exactly the GOOD version: named,
    typed measurements and flags, with the level(s) involved and full
    provenance -- never a bare confidence score standing in for
    "how much should Brain trust this."

    Historical note kept deliberately, not decorative: `state` and
    `evidence`-as-notes are retained in a similar SHAPE to
    AgentResult's `state`/`evidence` fields specifically so a
    migration adapter (Step 6+) can be written without reinventing
    how lifecycle states or human-readable explanations are carried --
    see contract.py::AgentResult.state's own docstring, which already
    reserved a state field for "a future migration path."
    """
    source: str                          # module id, e.g. "breakout", "fair_value_gap"
    observation_type: str                # e.g. "BREAKOUT", "FVG", "SWING_HIGH", "ORDER_BLOCK",
                                          # "LIQUIDITY_SWEEP", "MOMENTUM_READING", "TREND_READING"
    timeframe: str
    provenance: Provenance

    state: str = "NONE"                  # lifecycle token, e.g. "TRIGGERED", "CONFIRMED", "FAILED",
                                          # "MITIGATED" -- same vocabulary AgentResult evidence already
                                          # encodes via "State: X" lines (see brain/evidence.py), now a
                                          # real field instead of a string parsed out of a text list.

    measurements: List[Measurement] = field(default_factory=list)
    levels: List[ObservationLevel] = field(default_factory=list)
    flags: Dict[str, bool] = field(default_factory=dict)   # e.g. {"follow_through": True, "retest": False}
    tags: List[str] = field(default_factory=list)          # e.g. ["BULLISH", "HIGH_VOLUME"] -- descriptive,
                                                            # NOT a LONG/SHORT vote substitute
    history: List[AnalysisEvent] = field(default_factory=list)  # recent relevant event sequence, if any
    notes: List[str] = field(default_factory=list)         # human-readable explanation lines --
                                                            # same role AgentResult.evidence plays today
    valid: bool = True

    # --- convenience accessors -------------------------------------------

    def measurement(self, name: str, default: Optional[float] = None) -> Optional[float]:
        """Look up a named measurement's value. Returns `default` if
        not present -- mirrors dict.get()'s ergonomics without forcing
        callers to hold a real dict (measurements stay a typed list so
        unit metadata travels with each value, not just its number)."""
        for m in self.measurements:
            if m.name == name:
                return m.value
        return default

    def level_by_role(self, role: str) -> Optional[ObservationLevel]:
        """First level with the given role (e.g. "origin"). Replaces
        scoring.py's keyword-substring guesswork
        (_ORIGIN_LABEL_HINTS) with a direct, unambiguous lookup."""
        for lv in self.levels:
            if lv.role == role:
                return lv
        return None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe serialization, mirroring AgentResult.to_dict()'s
        role: this is what would flow into the API response, decision
        log, and (eventually) a redesigned frontend -- same
        contract-boundary concept as today, different shape."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Migration-compatibility sketch (NOT implemented -- see design doc
# section G). Left here as a documented placeholder, not a working
# adapter, so Step 6 has a concrete starting point without this step
# overstepping into implementation.
# ---------------------------------------------------------------------------

def to_agent_result_compat(observation: "AnalysisObservation", *args, **kwargs):
    """PLACEHOLDER -- intentionally not implemented in Step 5.

    The eventual adapter needs a DIRECTION INTERPRETER, not just a
    field remapping: AnalysisObservation deliberately carries no
    LONG/SHORT vote, so producing a legacy AgentResult means some
    explicit, documented rule set has to translate e.g. "BULLISH
    breakout, confirmed, high volume" into AgentResult(direction=LONG,
    confidence=X, strength=Y) for the CURRENT (pre-Step-8) Brain to
    keep consuming during the transition. That rule set is exactly
    the kind of "vote logic" the migration is trying to move OUT of
    the analytical modules and INTO an explicit, reviewable place --
    so it should be designed and reviewed on its own in Step 6, once
    there is a real migrated agent (Breakout) to validate it against,
    not guessed at here in the abstract.
    """
    raise NotImplementedError(
        "to_agent_result_compat is a Step 5 design placeholder only. "
        "Implement as part of Step 6's first agent migration (Breakout), "
        "once there is real observation data to validate the direction-"
        "interpretation rules against."
    )