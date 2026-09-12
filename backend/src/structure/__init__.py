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

BREAK_DISTANCE_CAP = 1.5  # ATR distance at which break-quality scoring
# maxes out — same style/caveat as LEVEL_BREAK_ATR_CAP in the Breakout
# agent (unvalidated, named and adjustable, not a proven-optimal number).


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

    # Break displacement — smoothly scaled by ATR-normalized distance,
    # not a coarse step function. Previously this only granted credit at
    # two fixed thresholds (0.5 and 1.0 ATR), meaning a 0.01 ATR break
    # and a 0.49 ATR break received identical (zero) credit despite
    # being meaningfully different in quality — a tiny break shouldn't
    # get the same treatment as a break that's most of the way to a
    # "meaningful" one. BREAK_DISTANCE_CAP is the ATR distance at which
    # this component maxes out; same "unvalidated but named and
    # findable" caveat as every other threshold in this codebase.
    confidence += clamp(break_distance_atr / BREAK_DISTANCE_CAP, 0.0, 1.0) * 12.0

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
    pivot_window_override: Optional[int] = None,
) -> AgentResult:
    """
    Analyze market structure using the shared MarketState.

    `market_state` is optional for backwards compatibility.

    If it is not supplied, the agent builds one locally from the same
    candles — in which case `pivot_window_override` lets a caller (e.g.
    the walk-forward backtest, for an A/B test) force the OLD fixed-5
    pivot window instead of the new per-timeframe dynamic default. Live
    trading never sets this, so it always gets the dynamic window.
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
                pivot_window_override=pivot_window_override,
            )
        )

    except Exception as exc:
        return neutral(
            AGENT_ID,
            timeframe,
            f"MarketState error: {exc}",
        )

    structure = state.structure

    # Direction now comes straight from the shared MarketState, which is
    # the single source of truth for it (see market_state/builder.py) —
    # this agent no longer recomputes its own separate direction from
    # hh/hl/lh/ll, which used to silently disagree with (and override)
    # the correctly-event-aware value builder.py had already worked out.
    direction = structure.direction

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

    evidence.append(
        f"Structure sequence: {structure.structure_sequence} | "
        f"Regime: {structure.regime} | "
        f"Latest event: {event if event != 'NONE' else 'none'} | "
        f"Break distance: {structure.break_distance_atr:.2f} ATR | "
        f"Direction: {direction} | "
        f"Structural confidence: {confidence:.0f}% | "
        f"Structural strength: {strength:.0f}%"
    )

    rev = structure.reversal
    if rev.state != "NONE":
        evidence.append(
            f"Reversal candidate: {rev.direction} | "
            f"CHoCH price: {rev.choch_price:.1f} | "
            f"Broken level: {rev.broken_level:.1f}"
        )
        level_hold_failed = rev.state == "FAILED" and "broken" in (rev.reason or "").lower()
        evidence.append(
            f"Level hold: {'FAIL' if level_hold_failed else 'PASS'} | "
            f"Follow-through: {rev.followthrough_atr:.2f} ATR | "
            f"Retest: {'PASS' if rev.retested else 'n/a'} | "
            f"New swing confirmed: {'YES' if rev.new_swing_confirmed else 'no'}"
        )
        evidence.append(
            f"Reversal confirmation score: {rev.score:.0f}/100 | "
            f"Reversal confidence: {rev.confidence:.0f}% | "
            f"Reversal strength: {rev.strength:.0f}% | "
            f"Bars since CHoCH: {rev.candles_since_choch}"
        )
        evidence.append(f"Reversal state: {rev.state}" + (f" ({rev.reason})" if rev.reason else ""))

    br = structure.bos_recovery
    if br.state != "NONE":
        evidence.append(
            f"Bearish BOS @ {br.broken_bos_level:.1f}" if br.direction == "LONG"
            else f"Bullish BOS @ {br.broken_bos_level:.1f}"
        )
        if br.bos_recovery:
            evidence.append(
                f"BOS Recovery: {'ACTIVE' if br.state not in ('FAILED', 'EXPIRED') else br.state} | "
                f"Recovery price: {br.bos_recovery_price:.1f} | "
                f"Age: {br.bos_recovery_age_bars} bars"
            )
            evidence.append(
                f"Recovery quality: {br.recovery_quality_score:.0f}/100 | "
                f"Penetration: {br.recovery_penetration_atr:.2f} ATR | "
                f"Body: {br.recovery_body_quality:.0%} | "
                f"Displacement: {br.recovery_displacement_atr:.2f} ATR | "
                f"Volume Z: {br.recovery_volume_quality:+.2f}"
            )
        if br.reversal_triggered:
            direction_word = "LONG" if br.direction == "LONG" else "SHORT"
            evidence.append(
                f"🟢 EARLY {direction_word} REVERSAL TRIGGER | "
                f"Trigger confidence: {br.reversal_trigger_confidence:.0f}%"
            )
            evidence.append(
                f"Confirmation developing: {br.reversal_confirmation_score:.0f}/100 | "
                f"Confirmation confidence: {br.reversal_confirmation_confidence:.0f}% | "
                f"Retest: {'PASS' if br.recovery_retest else 'pending'} | "
                f"Bars since trigger: {br.bars_since_trigger}"
            )
        if br.state in ("FAILED", "EXPIRED") and br.reason:
            evidence.append(f"{br.state}: {br.reason}")

    act = getattr(structure, "actionable_state", "NONE") or "NONE"
    lvl = getattr(structure, "actionable_level", None)
    adist = getattr(structure, "actionable_distance_atr", 0.0) or 0.0
    m5c = getattr(structure, "m5_confirm", "NONE") or "NONE"
    evidence.append(
        f"Actionable: {act} | Level: "
        f"{(f'{lvl:.1f}' if lvl is not None else 'n/a')} | "
        f"Distance: {adist:.2f} ATR | M5: {m5c}"
    )
    if getattr(structure, "developing_high", False):
        evidence.append("Developing: HIGH (not confirmed)")
    if getattr(structure, "developing_low", False):
        evidence.append("Developing: LOW (not confirmed)")
    evidence.append(f"State: {act}")

    return AgentResult(
        AGENT_ID,
        direction,
        round(confidence, 1),
        round(strength, 1),
        evidence,
        key_levels,
        timeframe,
        valid=True,
        state=act,
    )


from .observe import observe  # Step 6C — AnalysisObservation producer
