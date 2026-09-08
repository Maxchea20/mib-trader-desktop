from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .models import (
    LocationState,
    MarketState,
    ReversalCandidate,
    StructureEvent,
    StructureState,
    SwingPoint,
    VolatilityState,
)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------
# Dynamic pivot window
#
# A fixed candle-count window means something different on every
# timeframe: 5 candles on 1m is 5 minutes of noise-filtering, but 5
# candles on 1d is a full week. This scales the window so it represents
# a comparable SPAN OF REAL TIME across timeframes, anchored to 15m
# (the live trading timeframe) so nothing changes there.
#
# Anchor: 15m currently uses window=5, i.e. 5*15=75 minutes each side.
# Every other timeframe solves for the candle count that covers roughly
# that same 75-minute span, clamped to a sane range so 1m doesn't demand
# an absurdly long wait (75 candles) and 1d doesn't collapse to under 3
# (too few to call a real pivot).
# ---------------------------------------------------------------------
PIVOT_REFERENCE_MINUTES = 75
PIVOT_MIN_WINDOW = 3
PIVOT_MAX_WINDOW = 15

# --- Reversal Monitor V1 constants ---
# All illustrative/unvalidated starting parameters, same caveat as every
# other threshold in this codebase — named here so they're easy to find
# and tune once real data exists to calibrate against.
MAX_REVERSAL_CANDIDATE_BARS = 12  # max bars a candidate stays pending
# before expiring as FAILED — NOT a "wait N bars then confirm" timer,
# purely a stale-candidate safety net (see _track_reversal docstring).
MIN_MEANINGFUL_FOLLOWTHROUGH_ATR = 0.30
FOLLOWTHROUGH_FULL_ATR = 1.0     # follow-through score maxes out here
LEVEL_HOLD_FULL_ATR = 0.5        # level-hold score maxes out here
BREAK_QUALITY_FULL_ATR = 1.0     # break-quality score maxes out here
RETEST_TOLERANCE_ATR = 0.3       # price must come back within this much
# of the broken level (without closing through it) to count as a retest
REVERSAL_CONFIRMATION_THRESHOLD = 70.0
W_LEVEL_HOLD = 30.0
W_FOLLOWTHROUGH = 25.0
W_NEW_SWING = 30.0
W_BREAK_QUALITY = 15.0

_TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}


def pivot_window_for_timeframe(timeframe: str) -> int:
    """Returns the pivot left/right window size (each side) for a given
    timeframe. See module docstring above for the reasoning."""
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if not minutes:
        return 5  # unknown timeframe string — fall back to the old fixed default
    raw = PIVOT_REFERENCE_MINUTES / minutes
    return max(PIVOT_MIN_WINDOW, min(PIVOT_MAX_WINDOW, round(raw)))


def _get_arrays(candles) -> Dict[str, np.ndarray]:
    return {
        "ts": np.asarray([float(c["ts"]) for c in candles], dtype=float),
        "open": np.asarray([float(c["open"]) for c in candles], dtype=float),
        "high": np.asarray([float(c["high"]) for c in candles], dtype=float),
        "low": np.asarray([float(c["low"]) for c in candles], dtype=float),
        "close": np.asarray([float(c["close"]) for c in candles], dtype=float),
        "volume": np.asarray(
            [float(c.get("volume", 0.0)) for c in candles],
            dtype=float,
        ),
    }


def _find_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    left: int = 3,
    right: int = 3,
) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []

    if len(highs) < left + right + 1:
        return pivots

    for i in range(left, len(highs) - right):
        high_window = highs[i - left : i + right + 1]
        low_window = lows[i - left : i + right + 1]

        if highs[i] == np.max(high_window):
            pivots.append(
                {
                    "index": i,
                    "price": float(highs[i]),
                    "type": "HIGH",
                }
            )

        if lows[i] == np.min(low_window):
            pivots.append(
                {
                    "index": i,
                    "price": float(lows[i]),
                    "type": "LOW",
                }
            )

    pivots.sort(key=lambda x: x["index"])

    return pivots


def _calculate_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> float:
    if len(closes) < 2:
        return 0.0

    previous_close = closes[:-1]

    true_range = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - previous_close),
            np.abs(lows[1:] - previous_close),
        ),
    )

    if len(true_range) == 0:
        return 0.0

    return float(np.mean(true_range[-period:]))


def _calculate_swing_strength(
    price: float,
    surrounding_prices: List[float],
    atr: float,
) -> float:
    if atr <= 0 or not surrounding_prices:
        return 0.0

    distance = max(abs(price - p) for p in surrounding_prices)

    strength = (distance / atr) * 20.0

    return float(np.clip(strength, 0.0, 100.0))


def _track_reversal(events, swing_highs, swing_lows, high, low, close,
                     timestamps, pivot_right: int) -> "ReversalCandidate":
    """Reversal Monitor V1.

    A CHoCH is an early warning, not a confirmed reversal. This walks the
    SAME candle window that already produced `events` a second time,
    tracking whatever the most recent CHoCH has done since it fired:
    does price hold beyond the broken level, does it show real
    follow-through, and — the requirement that actually distinguishes a
    genuine transition from a false alarm — does a NEW confirmed swing
    subsequently form agreeing with the new direction (a fresh Higher
    Low after a bullish CHoCH, a fresh Lower High after a bearish one).

    This is a SEPARATE, ADDITIVE pass — it does not modify, and does not
    need to touch, the existing BOS/CHoCH detection loop at all. It only
    reads that loop's output (`events`) plus the already-confirmed swing
    lists, so the existing, tested detection logic is completely
    unaffected by this addition.

    Anti-lookahead: at loop position i, only candles [0..i] are ever
    read. A swing point only counts as "known" once its own pivot_right
    confirmation lag has passed (s.index + pivot_right <= i) — the exact
    same confirmation-timing rule the existing event loop already uses
    for activating swing highs/lows, applied here too so a reversal is
    never confirmed using a swing that wouldn't actually have been known
    yet at that point in history.

    Deliberately NOT the Breakout agent's follow-through code — the
    question here is different ("did structure actually transition,"
    not "did price clear a level convincingly"), so this is written
    fresh, even though the general shape (walk the given window forward
    once, no external state needed) is the same reasonable idea.
    """
    n = len(close)
    ts_to_idx = {int(ts): i for i, ts in enumerate(timestamps)}
    event_by_idx = {}
    for e in events:
        if e.timestamp is not None:
            idx = ts_to_idx.get(int(e.timestamp))
            if idx is not None:
                event_by_idx[idx] = e

    candidate = ReversalCandidate()

    for i in range(n):
        evt = event_by_idx.get(i)

        if evt is not None and evt.event == "CHoCH":
            # A fresh CHoCH always starts a NEW candidate — structure
            # has moved on again, so any prior still-pending candidate
            # is superseded rather than left dangling.
            atr_at_choch = _calculate_atr(high[:i + 1], low[:i + 1], close[:i + 1], 14) if i >= 15 else 0.0
            candidate = ReversalCandidate(
                state="CANDIDATE",
                direction=evt.direction,
                choch_timestamp=int(timestamps[i]),
                choch_price=float(close[i]),
                broken_level=evt.reference_price,
                origin_structure_direction=("SHORT" if evt.direction == "LONG" else "LONG"),
                atr_at_choch=atr_at_choch,
                choch_distance_atr=evt.distance_atr,
            )
            continue  # the CHoCH candle itself isn't "subsequent" yet

        if candidate.state != "CANDIDATE":
            continue

        choch_idx = ts_to_idx.get(candidate.choch_timestamp)
        if choch_idx is None or i <= choch_idx:
            continue

        c = float(close[i])
        candidate.candles_since_choch = i - choch_idx
        atr_ref = candidate.atr_at_choch

        if candidate.direction == "LONG":
            excursion = c - candidate.choch_price
            candidate.max_favorable_excursion = max(candidate.max_favorable_excursion, excursion)
            candidate.max_adverse_excursion = min(candidate.max_adverse_excursion, excursion)

            # A) Broken level hold — hard fail on close-through, per
            # spec: "do not treat a wick alone as confirmation" applies
            # symmetrically here — a wick below the level doesn't fail
            # the candidate either, only a CLOSE does.
            if c < candidate.broken_level:
                candidate.state = "FAILED"
                candidate.reason = "Close returned below broken LH"
                continue

            if atr_ref > 0 and not candidate.retested and (c - candidate.broken_level) / atr_ref <= RETEST_TOLERANCE_ATR:
                candidate.retested = True

            candidate.followthrough_atr = (c - candidate.choch_price) / atr_ref if atr_ref > 0 else 0.0
        else:  # SHORT
            excursion = candidate.choch_price - c
            candidate.max_favorable_excursion = max(candidate.max_favorable_excursion, excursion)
            candidate.max_adverse_excursion = min(candidate.max_adverse_excursion, excursion)

            if c > candidate.broken_level:
                candidate.state = "FAILED"
                candidate.reason = "Close returned above broken HL"
                continue

            if atr_ref > 0 and not candidate.retested and (candidate.broken_level - c) / atr_ref <= RETEST_TOLERANCE_ATR:
                candidate.retested = True

            candidate.followthrough_atr = (candidate.choch_price - c) / atr_ref if atr_ref > 0 else 0.0

        # C) New structural swing — must be CONFIRMED as of candle i
        # (respecting the same pivot_right lag the rest of the system
        # uses), and must itself form the correct HL/LH relative to the
        # swing immediately before it in the sequence.
        if not candidate.new_swing_confirmed:
            if candidate.direction == "LONG":
                relevant = [s for s in swing_lows if s.index > choch_idx and s.index + pivot_right <= i]
                if relevant:
                    newest = relevant[-1]
                    prior = [s for s in swing_lows if s.index < newest.index]
                    if prior and newest.price > prior[-1].price:
                        candidate.new_swing_confirmed = True
            else:
                relevant = [s for s in swing_highs if s.index > choch_idx and s.index + pivot_right <= i]
                if relevant:
                    newest = relevant[-1]
                    prior = [s for s in swing_highs if s.index < newest.index]
                    if prior and newest.price < prior[-1].price:
                        candidate.new_swing_confirmed = True

        # D) Confirmation score — a quality measurement, not a
        # replacement for the structural swing requirement below.
        level_hold_score = clamp(
            abs(c - candidate.broken_level) / atr_ref / LEVEL_HOLD_FULL_ATR, 0, 1
        ) * W_LEVEL_HOLD if atr_ref > 0 else 0.0
        followthrough_score = clamp(candidate.followthrough_atr / FOLLOWTHROUGH_FULL_ATR, 0, 1) * W_FOLLOWTHROUGH
        new_swing_score = W_NEW_SWING if candidate.new_swing_confirmed else 0.0
        break_quality_score = clamp(candidate.choch_distance_atr / BREAK_QUALITY_FULL_ATR, 0, 1) * W_BREAK_QUALITY

        candidate.score = level_hold_score + followthrough_score + new_swing_score + break_quality_score

        # Confirmation requires BOTH the score threshold AND the
        # structural swing condition — score alone is not sufficient,
        # per the explicit requirement that this not become "high score
        # without the market actually having transitioned."
        if candidate.score >= REVERSAL_CONFIRMATION_THRESHOLD and candidate.new_swing_confirmed:
            candidate.state = "CONFIRMED"
            candidate.reason = "Reversal confirmed"
            continue

        # Stale-candidate protection — a maximum validity window, not a
        # blind "wait N bars then confirm" timer. Confirmation still
        # only happens from the market-behavior conditions above; this
        # only prevents an indefinitely-pending candidate.
        if candidate.candles_since_choch >= MAX_REVERSAL_CANDIDATE_BARS:
            candidate.state = "FAILED"
            candidate.reason = "REVERSAL_CANDIDATE_EXPIRED"
            continue

    # Confidence/strength — deliberately different concepts. Confidence
    # never jumps straight to a high number just because a CHoCH fired;
    # it has to earn it through the score. Strength reflects the actual
    # size of the move achieved so far (MFE), independent of how
    # confident the evidence is that it's real.
    if candidate.state == "CANDIDATE":
        candidate.confidence = clamp(15.0 + candidate.score * 0.55, 0, 70)
    elif candidate.state == "CONFIRMED":
        candidate.confidence = clamp(50.0 + candidate.score * 0.44, 0, 94)
    else:
        candidate.confidence = 0.0

    if candidate.atr_at_choch > 0:
        candidate.strength = clamp(abs(candidate.max_favorable_excursion) / candidate.atr_at_choch * 25.0, 0, 100)
    else:
        candidate.strength = 0.0

    return candidate


def _build_structure(
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    close: float,
    atr: float,
    candles=None,
    pivot_right: int = 5,
) -> StructureState:
    state = StructureState()

    if len(swing_highs) >= 2:
        previous_high = swing_highs[-2]
        last_high = swing_highs[-1]

        state.previous_high = previous_high.price
        state.last_high = last_high.price

        state.hh = last_high.price > previous_high.price
        state.lh = last_high.price < previous_high.price

    if len(swing_lows) >= 2:
        previous_low = swing_lows[-2]
        last_low = swing_lows[-1]

        state.previous_low = previous_low.price
        state.last_low = last_low.price

        state.hl = last_low.price > previous_low.price
        state.ll = last_low.price < previous_low.price

    # ---------------------------------------------------------
    # Structural regime — purely from the swing SEQUENCE (HH/HL/
    # LH/LL). This is a classification of the swing SHAPE, and is
    # intentionally independent of directional bias — see below.
    # ---------------------------------------------------------

    if state.hh and state.hl:
        state.regime = "BULLISH"
    elif state.lh and state.ll:
        state.regime = "BEARISH"
    elif state.lh and state.hl:
        state.regime = "COMPRESSION"
    elif state.hh and state.ll:
        state.regime = "EXPANSION"
    else:
        state.regime = "TRANSITION"

    # ---------------------------------------------------------
    # Structure sequence
    # ---------------------------------------------------------

    if state.hh and state.hl:
        state.structure_sequence = "HH_HL"
    elif state.lh and state.ll:
        state.structure_sequence = "LH_LL"
    elif state.lh and state.hl:
        state.structure_sequence = "LH_HL"
    elif state.hh and state.ll:
        state.structure_sequence = "HH_LL"
    else:
        state.structure_sequence = "MIXED"

        # ---------------------------------------------------------
    # Historical BOS / CHoCH events
    #
    # Pivot-cross model:
    # - each confirmed swing becomes an active structural level
    # - a level can trigger only once
    # - close must CROSS the level
    # - current structure direction determines BOS vs CHoCH
    # ---------------------------------------------------------

    # REGIME != DIRECTION.
    #
    # A market can be structurally EXPANDING (HH+LL) or COMPRESSING
    # (LH+HL) while still carrying a clear directional bias from its
    # most recently confirmed break — regime describes the swing SHAPE,
    # direction describes CURRENT BIAS, and they are genuinely
    # independent questions. Previously this variable lived only inside
    # the `if candles:` block below, and its final value was discarded
    # entirely — direction was instead hardcoded to "NEUTRAL" for any
    # regime except pure BULLISH/BEARISH, silently throwing away exactly
    # the information this variable already tracked correctly.
    #
    # Equivalent to LuxAlgo's swingTrend.bias.
    structure_direction = "NEUTRAL"

    events: List[StructureEvent] = []

    if candles:
        closes = np.asarray(
            [float(c["close"]) for c in candles],
            dtype=float,
        )

        timestamps = np.asarray(
            [int(c["ts"]) for c in candles],
            dtype=np.int64,
        )

        # pivot_right now comes in as a function parameter — see the
        # module-level pivot_window_for_timeframe() and the caller in
        # build_market_state(). Previously this was a second, separately
        # hardcoded `= 5` here that had to be manually kept in sync with
        # the window passed to _find_pivots() elsewhere — an easy thing
        # to accidentally drift out of sync. Now there's exactly one
        # source of truth for this value.

        # Active structural pivots.
        active_high = None
        active_low = None

        high_crossed = False
        low_crossed = False

        previous_close = None

        # Process candles chronologically.
        for i in range(len(candles)):

            # -------------------------------------------------
            # Activate newly confirmed swing high.
            # -------------------------------------------------
            confirmed_highs = [
                s for s in swing_highs
                if s.index + pivot_right == i
            ]

            if confirmed_highs:
                active_high = confirmed_highs[-1]
                high_crossed = False

            # -------------------------------------------------
            # Activate newly confirmed swing low.
            # -------------------------------------------------
            confirmed_lows = [
                s for s in swing_lows
                if s.index + pivot_right == i
            ]

            if confirmed_lows:
                active_low = confirmed_lows[-1]
                low_crossed = False

            close_now = closes[i]

            if previous_close is None:
                previous_close = close_now
                continue

            # -------------------------------------------------
            # Bullish break of active swing high
            #
            # Previous close <= level
            # Current close > level
            #
            # Bearish structure -> CHoCH
            # Otherwise         -> BOS
            # -------------------------------------------------
            if (
                active_high is not None
                and not high_crossed
                and previous_close <= active_high.price
                and close_now > active_high.price
            ):
                event_type = (
                    "CHoCH"
                    if structure_direction == "SHORT"
                    else "BOS"
                )

                distance_atr = (
                    abs(close_now - active_high.price) / atr
                    if atr > 0
                    else 0.0
                )

                events.append(
                    StructureEvent(
                        event=event_type,
                        direction="LONG",
                        price=float(close_now),
                        timestamp=int(timestamps[i]),
                        reference_price=active_high.price,
                        swing_index=active_high.index,
                        distance_atr=round(
                            distance_atr,
                            3,
                        ),
                    )
                )

                high_crossed = True
                structure_direction = "LONG"

            # -------------------------------------------------
            # Bearish break of active swing low
            #
            # Previous close >= level
            # Current close < level
            #
            # Bullish structure -> CHoCH
            # Otherwise         -> BOS
            # -------------------------------------------------
            if (
                active_low is not None
                and not low_crossed
                and previous_close >= active_low.price
                and close_now < active_low.price
            ):
                event_type = (
                    "CHoCH"
                    if structure_direction == "LONG"
                    else "BOS"
                )

                distance_atr = (
                    abs(close_now - active_low.price) / atr
                    if atr > 0
                    else 0.0
                )

                events.append(
                    StructureEvent(
                        event=event_type,
                        direction="SHORT",
                        price=float(close_now),
                        timestamp=int(timestamps[i]),
                        reference_price=active_low.price,
                        swing_index=active_low.index,
                        distance_atr=round(
                            distance_atr,
                            3,
                        ),
                    )
                )

                low_crossed = True
                structure_direction = "SHORT"

            previous_close = close_now

        # Keep latest useful structural events.
        state.events = events[-50:]

    # ---------------------------------------------------------
    # Direction — resolved from the latest confirmed BOS/CHoCH when one
    # exists (that's what `structure_direction` ends up holding after
    # the event loop above), falling back to swing-sequence inference
    # only when no directional event has occurred at all. This is the
    # fix: direction now reflects actual recent price action, not just
    # which of the four swing-shape buckets the regime happens to fall
    # into.
    # ---------------------------------------------------------

    if structure_direction != "NEUTRAL":
        state.direction = structure_direction
    elif state.hh and state.hl:
        state.direction = "LONG"
    elif state.lh and state.ll:
        state.direction = "SHORT"
    else:
        state.direction = "NEUTRAL"

    # ---------------------------------------------------------
    # Current/latest structure event
    # ---------------------------------------------------------

    if state.events:
        state.event = state.events[-1]
        state.break_distance_atr = state.event.distance_atr
    else:
        state.event = StructureEvent(
            event="NONE",
            direction=state.direction,
            price=close,
            timestamp=(
                int(swing_highs[-1].timestamp)
                if swing_highs
                else (
                    int(swing_lows[-1].timestamp)
                    if swing_lows
                    else None
                )
            ),
        )
        state.break_distance_atr = 0.0

    return state


def _build_location(
    price: float,
    atr: float,
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
) -> LocationState:
    support = swing_lows[-1].price if swing_lows else None
    resistance = swing_highs[-1].price if swing_highs else None

    distance_support = None
    distance_resistance = None

    near_support = False
    near_resistance = False

    if atr > 0 and support is not None:
        distance_support = abs(price - support) / atr
        near_support = distance_support <= 1.0

    if atr > 0 and resistance is not None:
        distance_resistance = abs(price - resistance) / atr
        near_resistance = distance_resistance <= 1.0

    return LocationState(
        support=round(support, 6) if support is not None else None,
        resistance=round(resistance, 6)
        if resistance is not None
        else None,
        distance_to_support_atr=(
            round(distance_support, 3)
            if distance_support is not None
            else None
        ),
        distance_to_resistance_atr=(
            round(distance_resistance, 3)
            if distance_resistance is not None
            else None
        ),
        near_support=near_support,
        near_resistance=near_resistance,
        liquidity_high=(
            round(resistance, 6)
            if resistance is not None
            else None
        ),
        liquidity_low=(
            round(support, 6)
            if support is not None
            else None
        ),
    )


def _detect_market_phase(
    structure: StructureState,
    volatility: VolatilityState,
) -> str:
    if structure.regime == "COMPRESSION":
        return "COMPRESSION"

    if structure.event.event in ("BOS", "CHoCH"):
        return "EXPANSION"

    if structure.regime == "BULLISH":
        return "TREND_UP"

    if structure.regime == "BEARISH":
        return "TREND_DOWN"

    return "TRANSITION"


def build_market_state(
    candles,
    symbol: str,
    timeframe: str,
    pivot_window_override: Optional[int] = None,
) -> MarketState:
    if len(candles) < 30:
        raise ValueError(
            f"MarketState requires at least 30 candles, got {len(candles)}"
        )

    a = _get_arrays(candles)

    close = float(a["close"][-1])
    timestamp = int(a["ts"][-1])

    # ---------------------------------------------------------
    # ATR
    # ---------------------------------------------------------

    atr = _calculate_atr(
        a["high"],
        a["low"],
        a["close"],
        period=14,
    )

    atr_pct = (atr / close * 100.0) if close > 0 else 0.0

    # ---------------------------------------------------------
    # Swing detection
    #
    # Window size is dynamic by default — scaled per-timeframe by
    # pivot_window_for_timeframe() rather than a single fixed number
    # applied everywhere (see that function's docstring for why). Pass
    # pivot_window_override to force the OLD fixed-5 behavior instead,
    # e.g. for an A/B backtest comparing dynamic vs fixed.
    # ---------------------------------------------------------

    pivot_window = (
        pivot_window_override
        if pivot_window_override is not None
        else pivot_window_for_timeframe(timeframe)
    )

    pivots = _find_pivots(
        a["high"],
        a["low"],
        left=pivot_window,
        right=pivot_window,
    )

    swing_highs = [
        SwingPoint(
            index=p["index"],
            timestamp=int(a["ts"][p["index"]]),
            price=p["price"],
            kind="HIGH",
        )
        for p in pivots
        if p["type"] == "HIGH"
    ]

    swing_lows = [
        SwingPoint(
            index=p["index"],
            timestamp=int(a["ts"][p["index"]]),
            price=p["price"],
            kind="LOW",
        )
        for p in pivots
        if p["type"] == "LOW"
    ]

    # ---------------------------------------------------------
    # Swing strength
    # ---------------------------------------------------------

    if swing_highs:
        surrounding = [
            s.price for s in swing_highs[-5:-1]
        ]

        swing_highs[-1].strength = round(
            _calculate_swing_strength(
                swing_highs[-1].price,
                surrounding,
                atr,
            ),
            2,
        )

    if swing_lows:
        surrounding = [
            s.price for s in swing_lows[-5:-1]
        ]

        swing_lows[-1].strength = round(
            _calculate_swing_strength(
                swing_lows[-1].price,
                surrounding,
                atr,
            ),
            2,
        )

    # ---------------------------------------------------------
    # Structure
    # ---------------------------------------------------------

    structure = _build_structure(
    swing_highs=swing_highs,
    swing_lows=swing_lows,
    close=close,
    atr=atr,
    candles=candles,
    pivot_right=pivot_window,
)

    # Reversal Monitor V1 — separate, additive pass reading the events
    # _build_structure() just produced. Never modifies anything about
    # the existing structure/event computation above; if this ever
    # threw, it would only affect state.structure.reversal, not
    # direction/regime/event/hh/hl/lh/ll or the events list itself.
    if candles and len(candles) >= 30:
        try:
            _ts = np.asarray([int(c["ts"]) for c in candles], dtype=np.int64)
            structure.reversal = _track_reversal(
                events=structure.events,
                swing_highs=swing_highs,
                swing_lows=swing_lows,
                high=a["high"], low=a["low"], close=a["close"],
                timestamps=_ts,
                pivot_right=pivot_window,
            )
        except Exception:
            pass  # reversal monitor failure must never break structure itself

    if swing_highs:
        structure.swing_high_strength = swing_highs[-1].strength

    if swing_lows:
        structure.swing_low_strength = swing_lows[-1].strength

    # ---------------------------------------------------------
    # Range
    # ---------------------------------------------------------

    lookback = min(20, len(a["close"]))

    range_high = float(np.max(a["high"][-lookback:]))
    range_low = float(np.min(a["low"][-lookback:]))

    range_size = range_high - range_low

    if range_size > 0:
        range_position_pct = (
            (close - range_low) / range_size
        ) * 100.0
    else:
        range_position_pct = 50.0

    # Compression compares current range against
    # the previous equivalent range.

    compression_pct = 0.0

    if len(a["close"]) >= 40:
        current_range = (
            np.max(a["high"][-20:])
            - np.min(a["low"][-20:])
        )

        previous_range = (
            np.max(a["high"][-40:-20])
            - np.min(a["low"][-40:-20])
        )

        if previous_range > 0:
            compression_pct = (
                1.0
                - current_range / previous_range
            ) * 100.0

    volatility = VolatilityState(
        atr=round(atr, 6),
        atr_pct=round(atr_pct, 6),
        range_high=round(range_high, 6),
        range_low=round(range_low, 6),
        range_position_pct=round(
            float(np.clip(range_position_pct, 0.0, 100.0)),
            2,
        ),
        compression_pct=round(
            float(np.clip(compression_pct, -100.0, 100.0)),
            2,
        ),
    )

    # ---------------------------------------------------------
    # Location
    # ---------------------------------------------------------

    location = _build_location(
        price=close,
        atr=atr,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
    )

    # ---------------------------------------------------------
    # Market phase
    # ---------------------------------------------------------

    market_phase = _detect_market_phase(
        structure,
        volatility,
    )

    # ---------------------------------------------------------
    # Final shared state
    # ---------------------------------------------------------

    return MarketState(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        price=close,
        structure=structure,
        volatility=volatility,
        location=location,
        swing_highs=swing_highs[-20:],
        swing_lows=swing_lows[-20:],
        support=(
            [round(swing_lows[-1].price, 6)]
            if swing_lows
            else []
        ),
        resistance=(
            [round(swing_highs[-1].price, 6)]
            if swing_highs
            else []
        ),
        market_phase=market_phase,
        metadata={
            "candle_count": len(candles),
            "pivot_count": len(pivots),
            "range_lookback": lookback,
        },
    )