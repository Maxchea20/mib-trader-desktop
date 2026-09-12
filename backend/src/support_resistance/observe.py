"""Step 6B — Support/Resistance AnalysisObservation producer.

Detection and scoring stay in __init__.analyze(). This module only
publishes the facts that engine already computes.
"""
import numpy as np

from ..indicators import arrays, atr, ema as _ema, find_pivots, cluster_levels
from ..observation import (
    AnalysisEvent,
    AnalysisObservation,
    Measurement,
    ObservationLevel,
    Provenance,
)
from . import (
    AGENT_ID,
    APPROACH_ZONE_ATR,
    AT_ZONE_ATR,
    FLIP_HOLD_BARS,
    MIN_CANDLES,
    MIN_PIVOTS,
    PIVOT_LEFT,
    PIVOT_RIGHT,
    REACTION_LOOKBACK,
    REACTION_MIN_ATR,
    _atr_tolerance_pct,
    _enrich_zones,
    _zone_strength,
)

# LuxAlgo Support Resistance.pine defaults — comparison only.
TV_LEFT = 15
TV_RIGHT = 15
TV_VOLUME_THRESH = 20.0


def _tv_volume_oscillator(volume: np.ndarray) -> float:
    """Exact LuxAlgo formula: 100 * (ema(vol,5) - ema(vol,10)) / ema(vol,10)."""
    if len(volume) < 10:
        return 0.0
    short = _ema(volume, 5)
    long = _ema(volume, 10)
    last_long = float(long[-1])
    if abs(last_long) <= 1e-12:
        return 0.0
    return float(100.0 * (float(short[-1]) - last_long) / last_long)


def observe(candles, timeframe: str) -> AnalysisObservation:
    """Native AnalysisObservation for Support/Resistance."""
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None

    def _prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="indicators.find_pivots",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < MIN_CANDLES:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="SUPPORT_RESISTANCE",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=["Not enough candles"], valid=False,
        )

    a = arrays(candles)
    high, low, close, volume = a["high"], a["low"], a["close"], a["volume"]
    ts = a["ts"]
    n = len(close)
    idx = n - 1
    c = float(close[idx])
    last_ts = int(ts[-1])

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        return AnalysisObservation(
            source=AGENT_ID, observation_type="SUPPORT_RESISTANCE",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["Invalid ATR"], valid=False,
        )

    piv = find_pivots(high, low, left=PIVOT_LEFT, right=PIVOT_RIGHT)
    if len(piv) < MIN_PIVOTS:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="SUPPORT_RESISTANCE",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["Too few pivots"], valid=False,
        )

    prices = [p["price"] for p in piv]
    tolerance_pct = _atr_tolerance_pct(_atr, c)
    zones = cluster_levels(prices, tolerance_pct)
    zones = _enrich_zones(zones, piv, high, low, close, _atr, idx)
    if not zones:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="SUPPORT_RESISTANCE",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["No valid zones after enrichment"], valid=False,
        )

    for z in zones:
        z["strength"] = _zone_strength(z)

    supports = sorted([z for z in zones if z["price"] < c], key=lambda z: -z["price"])
    resistances = sorted([z for z in zones if z["price"] >= c], key=lambda z: z["price"])
    nearest_support = supports[0] if supports else None
    nearest_resistance = resistances[0] if resistances else None

    dist_sup_atr = (c - nearest_support["price"]) / _atr if nearest_support else 999.0
    dist_res_atr = (nearest_resistance["price"] - c) / _atr if nearest_resistance else 999.0

    zone, zone_dist_atr, zone_side = None, 999.0, None
    if dist_sup_atr <= dist_res_atr and nearest_support is not None:
        zone, zone_dist_atr, zone_side = nearest_support, dist_sup_atr, "support"
    elif nearest_resistance is not None:
        zone, zone_dist_atr, zone_side = nearest_resistance, dist_res_atr, "resistance"

    reaction = None
    reaction_displacement_atr = 0.0
    broken = False
    if zone is not None:
        if zone_side == "support":
            entered = float(np.min(low[-REACTION_LOOKBACK:])) <= zone["high"]
            closed_back_above = c > zone["high"]
            reaction_displacement_atr = (c - zone["low"]) / _atr
            if entered and closed_back_above and reaction_displacement_atr >= REACTION_MIN_ATR:
                reaction = "bullish_rejection"
            broken = c < zone["low"] - (0.1 * _atr)
        else:
            entered = float(np.max(high[-REACTION_LOOKBACK:])) >= zone["low"]
            closed_back_below = c < zone["low"]
            reaction_displacement_atr = (zone["high"] - c) / _atr
            if entered and closed_back_below and reaction_displacement_atr >= REACTION_MIN_ATR:
                reaction = "bearish_rejection"
            broken = c > zone["high"] + (0.1 * _atr)

    flip_context = None
    if zone is not None and broken and n > FLIP_HOLD_BARS:
        held = True
        for k in range(idx - FLIP_HOLD_BARS + 1, idx + 1):
            if zone_side == "support" and close[k] >= zone["low"]:
                held = False
                break
            if zone_side == "resistance" and close[k] <= zone["high"]:
                held = False
                break
        if held:
            flip_context = ("former support, now potential resistance" if zone_side == "support"
                            else "former resistance, now potential support")

    if zone is None:
        state = "NO_NEARBY_LEVEL"
    elif broken and flip_context:
        state = "RETESTING_BROKEN_SUPPORT" if zone_side == "support" else "RETESTING_BROKEN_RESISTANCE"
    elif broken:
        state = "SUPPORT_BROKEN" if zone_side == "support" else "RESISTANCE_BROKEN"
    elif reaction == "bullish_rejection":
        state = "SUPPORT_REJECTION"
    elif reaction == "bearish_rejection":
        state = "RESISTANCE_REJECTION"
    elif zone_dist_atr <= AT_ZONE_ATR:
        state = "AT_SUPPORT" if zone_side == "support" else "AT_RESISTANCE"
    elif zone_dist_atr <= APPROACH_ZONE_ATR:
        state = "APPROACHING_SUPPORT" if zone_side == "support" else "APPROACHING_RESISTANCE"
    else:
        state = "BETWEEN_LEVELS"

    event_ts = None
    if zone is not None:
        li = int(zone["latest_idx"])
        if 0 <= li < n:
            event_ts = int(ts[li])

    tv_osc = _tv_volume_oscillator(volume)
    tv_piv = find_pivots(high, low, left=TV_LEFT, right=TV_RIGHT)
    tv_highs = [p for p in tv_piv if p["type"] == "H"]
    tv_lows = [p for p in tv_piv if p["type"] == "L"]
    tv_last_h = float(tv_highs[-1]["price"]) if tv_highs else 0.0
    tv_last_l = float(tv_lows[-1]["price"]) if tv_lows else 0.0

    measurements = [
        Measurement("atr", float(_atr), unit="price", origin="mib"),
        Measurement("tolerance_pct", float(tolerance_pct), unit="pct", origin="mib"),
        Measurement("zone_count", float(len(zones)), unit="count", origin="mib"),
        Measurement("pivot_count", float(len(piv)), unit="count", origin="mib"),
        Measurement("pivot_left", float(PIVOT_LEFT), unit="bars", origin="mib"),
        Measurement("pivot_right", float(PIVOT_RIGHT), unit="bars", origin="mib"),
        Measurement("confirmation_lag_bars", float(PIVOT_RIGHT), unit="bars", origin="mib"),
        Measurement("tv_volume_osc", round(tv_osc, 6), unit="pct", origin="tradingview_luxalgo"),
        Measurement("tv_volume_threshold", TV_VOLUME_THRESH, unit="pct", origin="tradingview_luxalgo"),
        Measurement("tv_pivot_left", float(TV_LEFT), unit="bars", origin="tradingview_luxalgo"),
        Measurement("tv_pivot_right", float(TV_RIGHT), unit="bars", origin="tradingview_luxalgo"),
        Measurement("tv_pivot_extra_offset_bars", 1.0, unit="bars", origin="tradingview_luxalgo"),
        Measurement("tv_last_pivot_high", tv_last_h, unit="price", origin="tradingview_luxalgo"),
        Measurement("tv_last_pivot_low", tv_last_l, unit="price", origin="tradingview_luxalgo"),
    ]
    if nearest_support is not None:
        measurements.append(Measurement("distance_to_support_atr", round(dist_sup_atr, 6), unit="ATR", origin="mib"))
    if nearest_resistance is not None:
        measurements.append(Measurement("distance_to_resistance_atr", round(dist_res_atr, 6), unit="ATR", origin="mib"))
    if zone is not None:
        measurements.append(Measurement("nearest_zone_price", float(zone["price"]), unit="price", origin="mib"))
        measurements.append(Measurement("touch_count", float(zone["count"]), unit="count", origin="mib"))
        measurements.append(Measurement("recency_bars", float(zone["recency_bars"]), unit="bars", origin="mib"))
        measurements.append(Measurement("recency_score", float(zone["recency_score"]), unit="ratio", origin="mib"))
        measurements.append(Measurement("reaction_quality_atr", float(zone["avg_reaction_atr"]), unit="ATR", origin="mib"))
        measurements.append(Measurement("zone_width", float(zone["high"] - zone["low"]), unit="price", origin="mib"))
        measurements.append(Measurement("distance_atr", round(float(zone_dist_atr), 6), unit="ATR", origin="mib"))
        if reaction:
            measurements.append(Measurement("reaction_displacement_atr",
                                            round(float(reaction_displacement_atr), 6), unit="ATR", origin="mib"))

    levels = []
    for z in supports[:3]:
        levels.append(ObservationLevel(
            label=f"Support ({z['count']}x)",
            price=round(z["price"], 2),
            level_type="support",
            role="origin" if zone is z else "reference",
            timeframe=timeframe,
            upper=round(z["high"], 2),
            lower=round(z["low"], 2),
        ))
    for z in resistances[:3]:
        levels.append(ObservationLevel(
            label=f"Resistance ({z['count']}x)",
            price=round(z["price"], 2),
            level_type="resistance",
            role="origin" if zone is z else "reference",
            timeframe=timeframe,
            upper=round(z["high"], 2),
            lower=round(z["low"], 2),
        ))

    flags = {
        "closed_candle": True,
        "has_support": nearest_support is not None,
        "has_resistance": nearest_resistance is not None,
        "at_zone": bool(zone is not None and zone_dist_atr <= AT_ZONE_ATR),
        "recently_tested": bool(zone is not None and zone["recency_bars"] <= REACTION_LOOKBACK),
        "broken": bool(broken),
        "reclaimed": bool(flip_context),
        "rejection": reaction is not None,
    }

    tags = []
    if zone_side:
        tags.append(zone_side.upper())
    if reaction:
        tags.append("REJECTION")
    if broken:
        tags.append("BROKEN")

    history = []
    if zone is not None and event_ts is not None:
        if broken:
            history.append(AnalysisEvent(
                event_type="SUPPORT_BROKEN" if zone_side == "support" else "RESISTANCE_BROKEN",
                direction="BEARISH" if zone_side == "support" else "BULLISH",
                timestamp=last_ts,
                price=c,
                reference_price=zone["price"],
                distance_atr=float(zone_dist_atr) if zone_dist_atr < 900 else 0.0,
                source=AGENT_ID,
                detection_timestamp=last_ts,
            ))
        if reaction:
            history.append(AnalysisEvent(
                event_type="REJECTION",
                direction="BULLISH" if reaction == "bullish_rejection" else "BEARISH",
                timestamp=last_ts,
                price=c,
                reference_price=zone["price"],
                distance_atr=float(reaction_displacement_atr),
                source=AGENT_ID,
                detection_timestamp=last_ts,
            ))
        history.append(AnalysisEvent(
            event_type="ZONE_PIVOT",
            direction="BEARISH" if zone_side == "resistance" else "BULLISH",
            timestamp=event_ts,
            price=float(zone["price"]),
            swing_index=int(zone["latest_idx"]),
            source=AGENT_ID,
            detection_timestamp=int(ts[min(int(zone["latest_idx"]) + PIVOT_RIGHT, n - 1)]),
        ))

    notes = [f"{len(zones)} clustered zone(s) detected (tolerance {tolerance_pct:.2f}%, ATR-derived)"]
    if zone is not None:
        notes.append(f"Nearest {zone_side}: {zone['low']:.1f}\u2013{zone['high']:.1f}")
        notes.append(f"{zone['count']} touch(es), recency score {zone['recency_score']:.2f}")
        notes.append(f"Distance: {zone_dist_atr:.2f} ATR")
        notes.append(f"State: {state}")

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="SUPPORT_RESISTANCE",
        timeframe=timeframe,
        provenance=_prov(event_timestamp=event_ts, candle_timestamp=last_ts),
        state=state,
        measurements=measurements,
        levels=levels,
        flags=flags,
        tags=tags,
        history=history,
        notes=notes,
        valid=zone is not None,
    )
