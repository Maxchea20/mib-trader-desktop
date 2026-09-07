"""AGENT 10 — FAIR VALUE GAP (ICT imbalance).

Detects 3-candle imbalances using a LuxAlgo-inspired model:
- Bullish FVG: current low > high two candles back
- Bearish FVG: current high < low two candles back
- Displacement/significance filtering
- ATR-normalized gap quality
- Fresh / partial / filled mitigation state
- Nearest relevant bullish/bearish zones
- Exposes the FVG creation timestamp for chart rendering
"""

import numpy as np

from ..contract import (
    AgentResult,
    LONG,
    SHORT,
    NEUTRAL,
    neutral,
    clamp,
)
from ..indicators import arrays


AGENT_ID = "fair_value_gap"


# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

MIN_FVG_PCT = 0.03
MIN_DISPLACEMENT_ATR = 0.35
MAX_FVGS = 20


def _atr(high, low, close, period=14):
    """Simple ATR used only for FVG quality filtering."""

    if len(close) < 2:
        return 0.0

    prev_close = np.roll(close, 1)

    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ),
    )

    tr[0] = high[0] - low[0]

    if len(tr) < period:
        return float(np.mean(tr))

    return float(np.mean(tr[-period:]))


def _mitigation_state(fvg, highs, lows, start_index):
    """
    Determine how much the FVG has been filled.

    Bullish FVG:
        top = current low
        bottom = high two candles back

    Bearish FVG:
        top = low two candles back
        bottom = current high

    States:
        fresh
        partial
        filled
    """

    lo = fvg["lo"]
    hi = fvg["hi"]
    direction = fvg["dir"]

    if start_index >= len(highs):
        return "fresh"

    future_highs = highs[start_index:]
    future_lows = lows[start_index:]

    if direction == "bull":
        deepest = float(np.min(future_lows))

        if deepest <= lo:
            return "filled"

        if deepest < hi:
            return "partial"

        return "fresh"

    deepest = float(np.max(future_highs))

    if deepest >= hi:
        return "filled"

    if deepest > lo:
        return "partial"

    return "fresh"


def analyze(candles, timeframe: str) -> AgentResult:

    if len(candles) < 30:
        return neutral(
            AGENT_ID,
            timeframe,
            "Not enough candles",
        )

    a = arrays(candles)

    high = np.asarray(a["high"], dtype=float)
    low = np.asarray(a["low"], dtype=float)
    close = np.asarray(a["close"], dtype=float)
    open_ = np.asarray(a["open"], dtype=float)

    current_price = float(close[-1])
    n = len(close)

    atr = _atr(
        high,
        low,
        close,
        period=14,
    )

    if atr <= 0:
        return neutral(
            AGENT_ID,
            timeframe,
            "Invalid ATR",
        )

    fvgs = []

    # =====================================================
    # 3-CANDLE FVG DETECTION
    #
    # Candle A = i-2
    # Candle B = i-1
    # Candle C = i
    #
    # Bullish:
    #     low[C] > high[A]
    #
    # Bearish:
    #     high[C] < low[A]
    # =====================================================

    for i in range(2, n):

        high_2 = high[i - 2]
        low_2 = low[i - 2]

        current_high = high[i]
        current_low = low[i]

        middle_open = open_[i - 1]
        middle_close = close[i - 1]

        middle_body = abs(
            middle_close - middle_open
        )

        gap = 0.0
        direction = None
        zone_lo = None
        zone_hi = None

        # -------------------------------------------------
        # BULLISH FVG
        # -------------------------------------------------

        if current_low > high_2:

            direction = "bull"

            zone_lo = float(high_2)
            zone_hi = float(current_low)

            gap = zone_hi - zone_lo

        # -------------------------------------------------
        # BEARISH FVG
        # -------------------------------------------------

        elif current_high < low_2:

            direction = "bear"

            zone_lo = float(current_high)
            zone_hi = float(low_2)

            gap = zone_hi - zone_lo

        else:
            continue

        if gap <= 0:
            continue

        # -------------------------------------------------
        # GAP SIZE
        # -------------------------------------------------

        gap_pct = (
            gap / current_price * 100
        )

        gap_atr = gap / atr

        # -------------------------------------------------
        # DISPLACEMENT
        # -------------------------------------------------

        displacement_atr = (
            middle_body / atr
        )

        # -------------------------------------------------
        # SIGNIFICANCE FILTER
        # -------------------------------------------------

        if gap_pct < MIN_FVG_PCT:
            continue

        if displacement_atr < MIN_DISPLACEMENT_ATR:
            continue

        fvg = {
            "dir": direction,
            "lo": zone_lo,
            "hi": zone_hi,
            "i": i,
            "size": gap,
            "size_pct": gap_pct,
            "gap_atr": gap_atr,
            "displacement_atr": displacement_atr,
        }

        # -------------------------------------------------
        # MITIGATION
        # -------------------------------------------------

        fvg["state"] = _mitigation_state(
            fvg,
            high,
            low,
            i + 1,
        )

        fvg["mitigated"] = (
            fvg["state"] != "fresh"
        )

        # -------------------------------------------------
        # QUALITY SCORE
        # -------------------------------------------------

        gap_quality = clamp(
            gap_atr * 35.0,
            0.0,
            40.0,
        )

        displacement_quality = clamp(
            displacement_atr * 25.0,
            0.0,
            30.0,
        )

        recency_quality = clamp(
            30.0
            - ((n - 1 - i) * 0.5),
            0.0,
            30.0,
        )

        state_bonus = {
            "fresh": 20.0,
            "partial": 8.0,
            "filled": 0.0,
        }[fvg["state"]]

        fvg["quality"] = clamp(
            gap_quality
            + displacement_quality
            + recency_quality
            + state_bonus,
            0.0,
            100.0,
        )

        fvgs.append(fvg)

    # =====================================================
    # NO FVG
    # =====================================================

    if not fvgs:
        return neutral(
            AGENT_ID,
            timeframe,
            "No significant FVGs",
            valid=True,
        )

    # Keep the most recent meaningful FVGs.
    fvgs = fvgs[-MAX_FVGS:]

    fresh = [
        f
        for f in fvgs
        if f["state"] == "fresh"
    ]

    partial = [
        f
        for f in fvgs
        if f["state"] == "partial"
    ]

    active = fresh + partial

    # =====================================================
    # IF NO ACTIVE FVG
    # =====================================================

    if not active:

        evidence = [
            f"{len(fvgs)} significant FVGs",
            "All detected FVGs fully mitigated",
        ]

        key_levels = []

        for f in fvgs[-4:]:

            timestamp = int(
                candles[f["i"]]["ts"]
            )

            key_levels.append(
                {
                    "label": (
                        "FVG bull"
                        if f["dir"] == "bull"
                        else "FVG bear"
                    ),
                    "price": round(
                        (f["lo"] + f["hi"]) / 2,
                        2,
                    ),
                    "low": round(f["lo"], 2),
                    "high": round(f["hi"], 2),
                    "timestamp": int(candles[f["i"]]["ts"]),
                    "type": (
                        "fvg_bullish"
                        if f["dir"] == "bull"
                        else "fvg_bearish"
                    ),
                    "mitigated": True,
                    "state": "filled",
                    "quality": round(
                        f["quality"],
                        1,
                    ),
                    "timestamp": timestamp,
                }
            )

        return AgentResult(
            AGENT_ID,
            NEUTRAL,
            20.0,
            10.0,
            evidence,
            key_levels,
            timeframe,
            valid=True,
        )

    # =====================================================
    # FIND NEAREST ACTIVE FVG
    # =====================================================

    nearest = min(
        active,
        key=lambda f: abs(
            (
                (f["lo"] + f["hi"])
                / 2
            )
            - current_price
        ),
    )

    mid = (
        nearest["lo"]
        + nearest["hi"]
    ) / 2

    # =====================================================
    # DISTANCE
    # =====================================================

    distance_pct = (
        abs(mid - current_price)
        / current_price
        * 100
    )

    distance_atr = (
        abs(mid - current_price)
        / atr
    )

    # =====================================================
    # DIRECTION
    # =====================================================

    if nearest["dir"] == "bull":
        direction = LONG
    else:
        direction = SHORT

    # =====================================================
    # CONFIDENCE
    # =====================================================

    confidence = (
        40.0
        + nearest["quality"] * 0.35
        - distance_atr * 8.0
    )

    if nearest["state"] == "fresh":
        confidence += 8.0

    elif nearest["state"] == "partial":
        confidence -= 5.0

    confidence = clamp(
        confidence,
        25.0,
        90.0,
    )

    # =====================================================
    # STRENGTH
    # =====================================================

    strength = (
        nearest["quality"] * 0.70
        + max(
            0.0,
            25.0 - distance_atr * 10.0,
        )
    )

    strength = clamp(
        strength,
        10.0,
        90.0,
    )

    # =====================================================
    # EVIDENCE
    # =====================================================

    evidence = [
        (
            f"{len(fvgs)} significant FVGs "
            f"({len(fresh)} fresh, "
            f"{len(partial)} partial)"
        )
    ]

    if nearest["dir"] == "bull":

        evidence.append(
            "Nearest bullish FVG "
            f"{nearest['lo']:.1f}–"
            f"{nearest['hi']:.1f} "
            "(support/demand)"
        )

    else:

        evidence.append(
            "Nearest bearish FVG "
            f"{nearest['lo']:.1f}–"
            f"{nearest['hi']:.1f} "
            "(resistance/supply)"
        )

    evidence.append(
        f"FVG size={nearest['gap_atr']:.2f} ATR"
    )

    evidence.append(
        f"Displacement={nearest['displacement_atr']:.2f} ATR"
    )

    evidence.append(
        f"State={nearest['state']}"
    )

    # =====================================================
    # KEY LEVELS
    # =====================================================

    key_levels = []

    ranked = sorted(
        active,
        key=lambda f: (
            abs(
                (
                    f["lo"]
                    + f["hi"]
                )
                / 2
                - current_price
            ),
            -f["quality"],
        ),
    )

    for f in ranked[:6]:

        timestamp = int(
            candles[f["i"]]["ts"]
        )

        key_levels.append(
            {
                "label": (
                    "FVG bull"
                    if f["dir"] == "bull"
                    else "FVG bear"
                ),
                "price": round(
                    (
                        f["lo"]
                        + f["hi"]
                    ) / 2,
                    2,
                ),
                "low": round(
                    f["lo"],
                    2,
                ),
                "high": round(
                    f["hi"],
                    2,
                ),
                "type": (
                    "fvg_bullish"
                    if f["dir"] == "bull"
                    else "fvg_bearish"
                ),
                "mitigated": f["mitigated"],
                "state": f["state"],
                "quality": round(
                    f["quality"],
                    1,
                ),
                "timestamp": timestamp,
            }
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