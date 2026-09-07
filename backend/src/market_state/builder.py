from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .models import (
    LocationState,
    MarketState,
    StructureEvent,
    StructureState,
    SwingPoint,
    VolatilityState,
)


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


def _build_structure(
    swing_highs: List[SwingPoint],
    swing_lows: List[SwingPoint],
    close: float,
    atr: float,
    candles=None,
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
    # Structural regime
    # ---------------------------------------------------------

    if state.hh and state.hl:
        state.direction = "LONG"
        state.regime = "BULLISH"
    elif state.lh and state.ll:
        state.direction = "SHORT"
        state.regime = "BEARISH"
    elif state.lh and state.hl:
        state.direction = "NEUTRAL"
        state.regime = "COMPRESSION"
    elif state.hh and state.ll:
        state.direction = "NEUTRAL"
        state.regime = "EXPANSION"
    else:
        state.direction = "NEUTRAL"
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

        pivot_right = 5

        # Active structural pivots.
        active_high = None
        active_low = None

        high_crossed = False
        low_crossed = False

        # Equivalent to LuxAlgo's swingTrend.bias.
        structure_direction = "NEUTRAL"

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
    # ---------------------------------------------------------

    pivots = _find_pivots(
        a["high"],
        a["low"],
        left=5,
        right=5,
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
)

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