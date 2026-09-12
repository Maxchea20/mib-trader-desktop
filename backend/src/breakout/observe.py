"""Step 6A — Breakout AnalysisObservation producer.

Detection stays in __init__.analyze(). This module only publishes facts.
"""
import numpy as np

from ..indicators import arrays, atr, ema as _ema
from ..observation import (
    AnalysisEvent,
    AnalysisObservation,
    Measurement,
    ObservationLevel,
    Provenance,
)
from ..contract import LONG, SHORT
from . import (
    AGENT_ID,
    LOOKBACK,
    BreakoutLifecycle,
    _find_active_breakout,
    _range_as_of,
    _rolling_atr_series,
    _track_breakout_lifecycle,
    _volume_zscore,
)


def _tv_volume_oscillator(volume: np.ndarray) -> float:
    """LuxAlgo Support/Resistance.pine volume % oscillator.

    short = ema(volume, 5); long = ema(volume, 10)
    osc = 100 * (short - long) / long

    Comparison measurement only. Not used for MiB detection.
    """
    if len(volume) < 10:
        return 0.0
    short = _ema(volume, 5)
    long = _ema(volume, 10)
    last_long = float(long[-1])
    if abs(last_long) <= 1e-12:
        return 0.0
    return float(100.0 * (float(short[-1]) - last_long) / last_long)


def _dir_word(direction: str) -> str:
    if direction == LONG:
        return "BULLISH"
    if direction == SHORT:
        return "BEARISH"
    return "NEUTRAL"


def observe(candles, timeframe: str) -> AnalysisObservation:
    """Native AnalysisObservation for Breakout. Same detection as analyze()."""
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None

    def _base_prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="breakout._find_active_breakout",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < 45:
        return AnalysisObservation(
            source=AGENT_ID,
            observation_type="BREAKOUT",
            timeframe=timeframe,
            provenance=_base_prov(),
            state="NONE",
            notes=["Not enough candles"],
            valid=False,
        )

    a = arrays(candles)
    high, low, close, open_, volume = a["high"], a["low"], a["close"], a["open"], a["volume"]
    ts = a["ts"]
    n = len(close)
    last_ts = int(ts[-1])
    last_close = float(close[-1])

    lifecycle = BreakoutLifecycle()
    try:
        lifecycle = _track_breakout_lifecycle(high, low, close, open_, volume, ts)
    except Exception:
        pass

    history = []
    if lifecycle.state not in ("NONE", "") and lifecycle.breakout_timestamp is not None:
        history.append(AnalysisEvent(
            event_type="BREAKOUT_DETECTED",
            direction=_dir_word(lifecycle.direction),
            timestamp=int(lifecycle.breakout_timestamp),
            price=float(lifecycle.breakout_price or 0.0),
            reference_price=lifecycle.breakout_level,
            distance_atr=float(lifecycle.current_distance_from_level_atr or 0.0),
            source=AGENT_ID,
            detection_timestamp=int(lifecycle.breakout_timestamp),
        ))
        if lifecycle.pullback_detected and lifecycle.pullback_timestamp is not None:
            history.append(AnalysisEvent(
                event_type="PULLBACK",
                direction=_dir_word(lifecycle.direction),
                timestamp=int(lifecycle.pullback_timestamp),
                price=last_close,
                reference_price=lifecycle.breakout_level,
                distance_atr=float(lifecycle.pullback_depth_atr or 0.0),
                source=AGENT_ID,
                detection_timestamp=int(lifecycle.pullback_timestamp),
            ))
        if lifecycle.retest_detected and lifecycle.retest_timestamp is not None:
            history.append(AnalysisEvent(
                event_type="RETEST",
                direction=_dir_word(lifecycle.direction),
                timestamp=int(lifecycle.retest_timestamp),
                price=last_close,
                reference_price=lifecycle.breakout_level,
                distance_atr=float(lifecycle.retest_distance_atr or 0.0),
                source=AGENT_ID,
                detection_timestamp=int(lifecycle.retest_timestamp),
            ))
        if lifecycle.continuation_detected:
            history.append(AnalysisEvent(
                event_type="CONTINUATION",
                direction=_dir_word(lifecycle.direction),
                timestamp=last_ts,
                price=last_close,
                reference_price=float(lifecycle.breakout_price or 0.0),
                distance_atr=float(lifecycle.continuation_distance_atr or 0.0),
                source=AGENT_ID,
                detection_timestamp=last_ts,
            ))
        if lifecycle.breakout_failed:
            history.append(AnalysisEvent(
                event_type="FAILURE",
                direction=_dir_word(lifecycle.direction),
                timestamp=last_ts,
                price=last_close,
                reference_price=lifecycle.breakout_level,
                source=AGENT_ID,
                detection_timestamp=last_ts,
            ))

    found = _find_active_breakout(high, low, close, n)

    if found is None:
        recent_high = float(np.max(high[-LOOKBACK - 1:-1]))
        recent_low = float(np.min(low[-LOOKBACK - 1:-1]))
        c = float(close[-1])
        h_last, l_last = float(high[-1]), float(low[-1])
        _atr_neutral = atr(high, low, close, 14)
        if _atr_neutral > 0:
            dist_to_resistance_atr = (recent_high - c) / _atr_neutral
            dist_to_support_atr = (c - recent_low) / _atr_neutral
            candle_range_atr = (h_last - l_last) / _atr_neutral
            vol_z = _volume_zscore(volume)
            atr_series = _rolling_atr_series(high, low, close, period=14, count=21)
            if len(atr_series) >= 2:
                atr_baseline = float(np.mean(atr_series[:-1]))
                atr_expansion = (_atr_neutral / atr_baseline) if atr_baseline > 0 else 1.0
            else:
                atr_expansion = 1.0
        else:
            dist_to_resistance_atr = dist_to_support_atr = candle_range_atr = vol_z = 0.0
            atr_expansion = 1.0

        wick_above = h_last > recent_high and c <= recent_high
        wick_below = l_last < recent_low and c >= recent_low
        tv_osc = _tv_volume_oscillator(volume)

        measurements = [
            Measurement("distance_to_resistance_atr", round(dist_to_resistance_atr, 6), unit="ATR", origin="mib"),
            Measurement("distance_to_support_atr", round(dist_to_support_atr, 6), unit="ATR", origin="mib"),
            Measurement("volume_zscore", round(float(vol_z), 6), unit="zscore", origin="mib"),
            Measurement("candle_range_atr", round(candle_range_atr, 6), unit="ATR", origin="mib"),
            Measurement("atr_expansion", round(atr_expansion, 6), unit="ratio", origin="mib"),
            Measurement("tv_volume_osc", round(tv_osc, 6), unit="pct", origin="tradingview_luxalgo"),
            Measurement("confirmation_lag_bars", 0.0, unit="bars", origin="mib"),
        ]
        flags = {
            "follow_through": False,
            "retest": bool(lifecycle.retest_detected),
            "failure": bool(lifecycle.breakout_failed),
            "wick_rejection": bool(wick_above or wick_below),
            "closed_candle": True,
        }
        tags = []
        if wick_above:
            tags.append("BULLISH_WICK_REJECTION")
        if wick_below:
            tags.append("BEARISH_WICK_REJECTION")
        notes = []
        if wick_above:
            notes.append("Bullish breakout rejection — wick above resistance, close back inside range")
        elif wick_below:
            notes.append("Bearish breakdown rejection — wick below support, close back inside range")
        else:
            notes.append(f"Price inside range ({recent_low:.1f}–{recent_high:.1f})")
            notes.append("No confirmed breakout")

        state = lifecycle.state if lifecycle.state != "NONE" else "NONE"
        if wick_above or wick_below:
            obs_type = "BREAKOUT_REJECTION"
            event_ts = last_ts
        else:
            obs_type = "RANGE"
            event_ts = None

        return AnalysisObservation(
            source=AGENT_ID,
            observation_type=obs_type,
            timeframe=timeframe,
            provenance=_base_prov(event_timestamp=event_ts, candle_timestamp=last_ts),
            state=state,
            measurements=measurements,
            levels=[
                ObservationLevel(label="Range High", price=round(recent_high, 2),
                                 level_type="resistance", role="reference", timeframe=timeframe),
                ObservationLevel(label="Range Low", price=round(recent_low, 2),
                                 level_type="support", role="reference", timeframe=timeframe),
            ],
            flags=flags,
            tags=tags,
            history=history,
            notes=notes,
            valid=True,
        )

    origin_idx, direction, level = found
    bars_since_origin = (n - 1) - origin_idx
    event_ts = int(ts[origin_idx])

    o_high, o_low, o_close = high[:origin_idx + 1], low[:origin_idx + 1], close[:origin_idx + 1]
    o_open, o_volume = open_[:origin_idx + 1], volume[:origin_idx + 1]
    _atr = atr(o_high, o_low, o_close, 14)
    if _atr <= 0:
        return AnalysisObservation(
            source=AGENT_ID,
            observation_type="BREAKOUT",
            timeframe=timeframe,
            provenance=_base_prov(event_timestamp=event_ts, candle_timestamp=event_ts),
            state="NONE",
            notes=["Invalid ATR"],
            valid=False,
        )

    c = float(close[origin_idx])
    o = float(open_[origin_idx])
    h = float(high[origin_idx])
    l = float(low[origin_idx])
    penetration = (c - level) if direction == LONG else (level - c)
    penetration_atr = penetration / _atr
    displacement_atr = abs(c - o) / _atr
    candle_range = max(h - l, 1e-9)
    body_ratio = abs(c - o) / candle_range
    wick_ratio = 1.0 - body_ratio
    close_location = (c - l) / candle_range if direction == LONG else (h - c) / candle_range
    vol_z = _volume_zscore(o_volume)
    range_atr = candle_range / _atr
    atr_series = _rolling_atr_series(o_high, o_low, o_close, period=14, count=21)
    if len(atr_series) >= 2:
        atr_baseline = float(np.mean(atr_series[:-1]))
        atr_expansion = (_atr / atr_baseline) if atr_baseline > 0 else 1.0
    else:
        atr_expansion = 1.0

    confirmations = 0
    for j in range(origin_idx + 1, n):
        extended = close[j] > c if direction == LONG else close[j] < c
        if extended:
            confirmations += 1

    tv_osc = _tv_volume_oscillator(o_volume)
    reliable = penetration_atr >= 0.5 or vol_z >= 1.0

    measurements = [
        Measurement("breakout_level", float(level), unit="price", origin="mib"),
        Measurement("breakout_price", c, unit="price", origin="mib"),
        Measurement("penetration", round(penetration, 6), unit="price", origin="mib"),
        Measurement("penetration_atr", round(penetration_atr, 6), unit="ATR", origin="mib"),
        Measurement("displacement_atr", round(displacement_atr, 6), unit="ATR", origin="mib"),
        Measurement("body_ratio", round(body_ratio, 6), unit="ratio", origin="mib"),
        Measurement("wick_ratio", round(wick_ratio, 6), unit="ratio", origin="mib"),
        Measurement("close_location", round(close_location, 6), unit="ratio", origin="mib"),
        Measurement("volume_zscore", round(float(vol_z), 6), unit="zscore", origin="mib"),
        Measurement("range_atr", round(range_atr, 6), unit="ATR", origin="mib"),
        Measurement("atr_expansion", round(atr_expansion, 6), unit="ratio", origin="mib"),
        Measurement("follow_through_bars", float(confirmations), unit="bars", origin="mib"),
        Measurement("confirmation_lag_bars", float(bars_since_origin), unit="bars", origin="mib"),
        Measurement("origin_index", float(origin_idx), unit="index", origin="mib"),
        Measurement("tv_volume_osc", round(tv_osc, 6), unit="pct", origin="tradingview_luxalgo"),
    ]
    if lifecycle.pullback_detected:
        measurements.append(Measurement("pullback_depth_atr", float(lifecycle.pullback_depth_atr), unit="ATR", origin="mib"))
    if lifecycle.retest_detected:
        measurements.append(Measurement("retest_distance_atr", float(lifecycle.retest_distance_atr), unit="ATR", origin="mib"))
    if lifecycle.continuation_detected:
        measurements.append(Measurement("continuation_distance_atr", float(lifecycle.continuation_distance_atr), unit="ATR", origin="mib"))

    level_type = "resistance" if direction == LONG else "support"
    flags = {
        "follow_through": confirmations > 0,
        "retest": bool(lifecycle.retest_detected),
        "failure": bool(lifecycle.breakout_failed),
        "wick_rejection": False,
        "closed_candle": True,
        "low_reliability": not reliable,
    }
    tags = [_dir_word(direction)]
    if confirmations > 0:
        tags.append("FOLLOW_THROUGH")
    if vol_z >= 1.0:
        tags.append("VOLUME_CONFIRM")

    state = lifecycle.state if lifecycle.state not in ("NONE", "") else (
        "CONFIRMED" if confirmations > 0 else "TRIGGERED"
    )

    notes = [
        f"{'Bullish' if direction == LONG else 'Bearish'} break of {level:.1f} "
        f"({'this candle' if bars_since_origin == 0 else str(bars_since_origin) + ' candle(s) ago'})",
        f"Level penetration: {penetration_atr:.2f} ATR",
        f"Displacement: {displacement_atr:.2f} ATR",
        f"Body ratio {body_ratio:.0%}, close location {close_location:.0%}",
        f"Volume Z-score {vol_z:+.2f}",
    ]

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="BREAKOUT",
        timeframe=timeframe,
        provenance=_base_prov(event_timestamp=event_ts, candle_timestamp=event_ts),
        state=state,
        measurements=measurements,
        levels=[
            ObservationLevel(
                label="Breakout Level",
                price=round(float(level), 2),
                level_type=level_type,
                role="origin",
                timeframe=timeframe,
            ),
        ],
        flags=flags,
        tags=tags,
        history=history,
        notes=notes,
        valid=reliable,
    )
