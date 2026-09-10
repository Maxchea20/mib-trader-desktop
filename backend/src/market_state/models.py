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
    state: str = "NONE"
    direction: str = "NEUTRAL"
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
class BosRecoveryState:
    state: str = "NONE"
    direction: str = "NEUTRAL"
    broken_bos_level: Optional[float] = None
    bos_timestamp: Optional[int] = None
    bos_distance_from_extreme_atr: float = 0.0
    bos_relevance_ok: bool = True
    bos_recovery: bool = False
    bos_recovery_timestamp: Optional[int] = None
    bos_recovery_price: Optional[float] = None
    bos_recovery_age_bars: int = 0
    recovery_quality_score: float = 0.0
    recovery_penetration_atr: float = 0.0
    recovery_body_quality: float = 0.0
    recovery_displacement_atr: float = 0.0
    recovery_volume_quality: float = 0.0
    recovery_followthrough: float = 0.0
    recovery_retest: bool = False
    reversal_triggered: bool = False
    reversal_trigger_price: Optional[float] = None
    reversal_trigger_timestamp: Optional[int] = None
    reversal_trigger_confidence: float = 0.0
    reversal_confirmation_score: float = 0.0
    reversal_confirmation_confidence: float = 0.0
    opposite_structural_level: Optional[float] = None
    bars_since_recovery: int = 0
    bars_since_trigger: int = 0
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
    event: StructureEvent = field(default_factory=lambda: StructureEvent(event="NONE", direction="NEUTRAL", price=0.0))
    events: List[StructureEvent] = field(default_factory=list)
    reversal: ReversalCandidate = field(default_factory=ReversalCandidate)
    bos_recovery: BosRecoveryState = field(default_factory=BosRecoveryState)
    actionable_state: str = "NONE"
    actionable_level: Optional[float] = None
    actionable_distance_atr: float = 0.0
    m5_confirm: str = "NONE"
    developing_high: bool = False
    developing_low: bool = False


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
