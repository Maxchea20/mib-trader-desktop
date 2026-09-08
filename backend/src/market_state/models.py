from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SwingPoint:
    index: int
    timestamp: int
    price: float
    kind: str  # "HIGH" or "LOW"
    strength: float = 0.0


@dataclass
class StructureEvent:
    event: str  # "BOS", "CHoCH", or "NONE"
    direction: str  # "LONG", "SHORT", or "NEUTRAL"
    price: float
    timestamp: Optional[int] = None
    reference_price: Optional[float] = None
    swing_index: Optional[int] = None
    distance_atr: float = 0.0


@dataclass
class ReversalCandidate:
    """A CHoCH is an early warning, not a confirmed reversal — this
    tracks whether it actually develops into one. See builder.py's
    _track_reversal() for the state machine. Separate from
    StructureState.direction/regime/event on purpose: a market can have
    a CANDIDATE reversal pending while its confirmed direction/regime
    haven't changed yet."""
    state: str = "NONE"  # NONE, CANDIDATE, CONFIRMED, FAILED
    direction: str = "NEUTRAL"  # LONG, SHORT, NEUTRAL
    choch_timestamp: Optional[int] = None
    choch_price: Optional[float] = None
    broken_level: Optional[float] = None
    origin_structure_direction: str = "NEUTRAL"
    atr_at_choch: float = 0.0
    choch_distance_atr: float = 0.0
    candles_since_choch: int = 0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    followthrough_atr: float = 0.0
    new_swing_confirmed: bool = False
    retested: bool = False
    score: float = 0.0
    confidence: float = 0.0
    strength: float = 0.0
    reason: str = ""


@dataclass
class StructureState:
    direction: str = "NEUTRAL"
    regime: str = "NEUTRAL"

    swing_high_strength: float = 0.0
    swing_low_strength: float = 0.0

    break_distance_atr: float = 0.0

    structure_sequence: str = "UNKNOWN"

    hh: bool = False
    hl: bool = False
    lh: bool = False
    ll: bool = False

    last_high: Optional[float] = None
    previous_high: Optional[float] = None

    last_low: Optional[float] = None
    previous_low: Optional[float] = None

    event: StructureEvent = field(
        default_factory=lambda: StructureEvent(
            event="NONE",
            direction="NEUTRAL",
            price=0.0,
        )
    )

    events: List[StructureEvent] = field(default_factory=list)

    reversal: ReversalCandidate = field(default_factory=ReversalCandidate)


@dataclass
class VolatilityState:
    atr: float = 0.0
    atr_pct: float = 0.0

    range_high: float = 0.0
    range_low: float = 0.0
    range_position_pct: float = 50.0

    compression_pct: float = 0.0


@dataclass
class LocationState:
    support: Optional[float] = None
    resistance: Optional[float] = None

    distance_to_support_atr: Optional[float] = None
    distance_to_resistance_atr: Optional[float] = None

    near_support: bool = False
    near_resistance: bool = False

    liquidity_high: Optional[float] = None
    liquidity_low: Optional[float] = None


@dataclass
class MarketState:
    symbol: str
    timeframe: str

    timestamp: int
    price: float

    structure: StructureState
    volatility: VolatilityState
    location: LocationState

    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows: List[SwingPoint] = field(default_factory=list)

    support: List[float] = field(default_factory=list)
    resistance: List[float] = field(default_factory=list)

    market_phase: str = "UNKNOWN"
    metadata: dict = field(default_factory=dict)