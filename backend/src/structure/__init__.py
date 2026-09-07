"""
AGENT 1 — MARKET STRUCTURE.

Consumes the shared MarketState structure map.

The MarketState builder is the single source of truth for:
- HH / HL / LH / LL
- structural regime
- BOS / CHoCH
- swing strength
- structural break distance
- support / resistance location

This agent converts that shared information into the standard
AgentResult contract consumed by the Brain.
"""

from typing import Optional

from ..contract import (
    AgentResult,
    LONG,
    SHORT,
    NEUTRAL,
    neutral,
    clamp,
)
from ..market_state.builder import build_market_state


AGENT_ID = "market_structure"


def _direction_from_structure(
    hh: bool,
    hl: bool,
    lh: bool,
    ll: bool,
) -> str:
    """
    Determine structural direction from the shared HH/HL/LH/LL state.

    Full structures have priority.

    Mixed structures remain neutral rather than forcing a directional
    interpretation.
    """

    if hh and hl:
        return LONG

    if lh and ll:
        return SHORT

    return NEUTRAL


def _structure_score(
    direction: str,
    hh: bool,
    hl: bool,
    lh: bool,
    ll: bool,
    event: str,
) -> int:
    """
    Convert structural evidence into a small deterministic score.

    This is intentionally separate from confidence.

    The Brain later combines this AgentResult with the other agents.
    """

    if direction == LONG:
        score = 0

        if hh:
            score += 1

        if hl:
            score += 1

        if event in ("BOS", "CHoCH"):
            score += 1

        return score

    if direction == SHORT:
        score = 0

        if lh:
            score += 1

        if ll:
            score += 1

        if event in ("BOS", "CHoCH"):
            score += 1

        return score

    return 0


def _confidence_from_state(
    direction: str,
    regime: str,
    event: str,
    swing_strength: float,
    break_distance_atr: float,
) -> float:
    """
    Calculate deterministic structural confidence.

    Confidence reflects the quality of the structural evidence,
    not whether an entry should happen.
    """

    if direction == NEUTRAL:
        return 30.0

    confidence = 45.0

    # Strong structural regime.
    if (
        (direction == LONG and regime == "BULLISH")
        or
        (direction == SHORT and regime == "BEARISH")
    ):
        confidence += 10.0

    # Structural event adds confirmation.
    if event == "BOS":
        confidence += 10.0

    elif event == "CHoCH":
        confidence += 8.0

    # Swing significance.
    confidence += min(float(swing_strength) * 0.12, 12.0)

    # Break displacement.
    if break_distance_atr >= 1.0:
        confidence += 8.0

    elif break_distance_atr >= 0.5:
        confidence += 4.0

    return clamp(confidence, 0.0, 95.0)


def _build_evidence(
    direction: str,
    structure,
) -> list:
    evidence = []

    hh = structure.hh
    hl = structure.hl
    lh = structure.lh
    ll = structure.ll

    if hh and hl:
        evidence.append(
            "Higher High + Higher Low → bullish structure"
        )

    elif lh and ll:
        evidence.append(
            "Lower High + Lower Low → bearish structure"
        )

    elif lh and hl:
        evidence.append(
            "Lower High + Higher Low → structural compression"
        )

    elif hh and ll:
        evidence.append(
            "Higher High + Lower Low → structural expansion"
        )

    else:
        evidence.append(
            f"Mixed structure → {structure.structure_sequence}"
        )

    event = structure.event

    if event.event == "BOS":
        if event.direction == LONG:
            evidence.append(
                "Bullish BOS: price broke the latest swing high"
            )

        elif event.direction == SHORT:
            evidence.append(
                "Bearish BOS: price broke the latest swing low"
            )

    elif event.event == "CHoCH":
        if event.direction == LONG:
            evidence.append(
                "CHoCH: structure shifted from bearish to bullish"
            )

        elif event.direction == SHORT:
            evidence.append(
                "CHoCH: structure shifted from bullish to bearish"
            )

    if event.distance_atr > 0:
        evidence.append(
            f"Structural break distance: "
            f"{event.distance_atr:.2f} ATR"
        )

    if structure.swing_high_strength > 0:
        evidence.append(
            f"Latest swing high strength: "
            f"{structure.swing_high_strength:.0f}%"
        )

    if structure.swing_low_strength > 0:
        evidence.append(
            f"Latest swing low strength: "
            f"{structure.swing_low_strength:.0f}%"
        )

    return evidence


def _build_key_levels(
    state,
) -> list:
    levels = []

    if state.location.resistance is not None:
        levels.append(
            {
                "label": "Swing High",
                "price": round(
                    state.location.resistance,
                    2,
                ),
                "type": "resistance",
            }
        )

    if state.location.support is not None:
        levels.append(
            {
                "label": "Swing Low",
                "price": round(
                    state.location.support,
                    2,
                ),
                "type": "support",
            }
        )

    return levels


def analyze(
    candles,
    timeframe: str,
    market_state: Optional[object] = None,
) -> AgentResult:
    """
    Analyze market structure using the shared MarketState.

    `market_state` is optional for backwards compatibility.

    If it is not supplied, the agent builds one locally from the same
    candles. This allows existing callers/tests to continue working
    while the main engine migrates to explicitly shared state.
    """

    if len(candles) < 30:
        return neutral(
            AGENT_ID,
            timeframe,
            "Not enough candles",
        )

    try:
        state = (
            market_state
            if market_state is not None
            else build_market_state(
                candles,
                symbol="UNKNOWN",
                timeframe=timeframe,
            )
        )

    except Exception as exc:
        return neutral(
            AGENT_ID,
            timeframe,
            f"MarketState error: {exc}",
        )

    structure = state.structure

    direction = _direction_from_structure(
        hh=structure.hh,
        hl=structure.hl,
        lh=structure.lh,
        ll=structure.ll,
    )

    event = structure.event.event

    score = _structure_score(
        direction=direction,
        hh=structure.hh,
        hl=structure.hl,
        lh=structure.lh,
        ll=structure.ll,
        event=event,
    )

    if direction == LONG:
        swing_strength = structure.swing_high_strength

    elif direction == SHORT:
        swing_strength = structure.swing_low_strength

    else:
        swing_strength = max(
            structure.swing_high_strength,
            structure.swing_low_strength,
        )

    confidence = _confidence_from_state(
        direction=direction,
        regime=structure.regime,
        event=event,
        swing_strength=swing_strength,
        break_distance_atr=structure.break_distance_atr,
    )

    evidence = _build_evidence(
        direction=direction,
        structure=structure,
    )

    evidence.append(
        f"Market phase: {state.market_phase}"
    )

    evidence.append(
        f"Range position: "
        f"{state.volatility.range_position_pct:.1f}%"
    )

    key_levels = _build_key_levels(state)

    strength = clamp(
        score * 22.0
        + min(swing_strength * 0.20, 20.0),
        0.0,
        100.0,
    )

    if direction == NEUTRAL:
        strength = clamp(
            min(swing_strength * 0.20, 30.0),
            0.0,
            100.0,
        )

    return AgentResult(
        AGENT_ID,
        direction,
        round(confidence, 1),
        round(strength, 1),
        evidence,
        key_levels,
        timeframe,
        valid=True,
    )