"""Step 6G — Volume V2 AnalysisObservation producer.

Candle traded-volume facts only. No order book.
analyze() remains source of truth for state.
"""
import numpy as np

from ..indicators import arrays, atr, rvol
from ..observation import (
    AnalysisObservation,
    Measurement,
    Provenance,
)
from . import (
    ABSORPTION_MAX_DISPLACEMENT_ATR,
    ABSORPTION_MIN_RVOL,
    AGENT_ID,
    BREAKOUT_LOOKBACK,
    DIRECTIONAL_LOOKBACK,
    DIVERGENCE_LOOKBACK,
    EFFICIENCY_RVOL_REF,
    EXHAUSTION_MIN_STREAK,
    EXPANSION_LOOKBACK,
    MIN_CANDLES,
    _participation_label,
    _rvol_series,
    _safe,
)


def observe(candles, timeframe: str) -> AnalysisObservation:
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None

    def _prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="volume.analyze",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < MIN_CANDLES:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="VOLUME",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=["Not enough candles"], valid=False,
        )

    a = arrays(candles)
    high, low, close, open_, volume = a["high"], a["low"], a["close"], a["open"], a["volume"]
    n = len(close)
    last_ts = int(candles[-1]["ts"])

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        return AnalysisObservation(
            source=AGENT_ID, observation_type="VOLUME",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["Invalid ATR"], valid=False,
        )
    if float(np.sum(volume[-20:])) <= 0:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="VOLUME",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["No volume data available"], valid=False,
        )

    _rvol = _safe(rvol(volume, 20), 1.0)
    participation = _participation_label(_rvol)

    rvol_hist = _rvol_series(volume, 20, EXPANSION_LOOKBACK)
    if len(rvol_hist) >= 4:
        half = len(rvol_hist) // 2
        change = float(np.mean(rvol_hist[half:])) - float(np.mean(rvol_hist[:half]))
        if change > 0.15:
            vol_trend = "EXPANDING"
        elif change < -0.15:
            vol_trend = "CONTRACTING"
        else:
            vol_trend = "STABLE"
    else:
        vol_trend, change = "STABLE", 0.0

    directional_sum = 0.0
    weight_sum = 0.0
    start = max(0, n - DIRECTIONAL_LOOKBACK)
    for i in range(start, n):
        rng = max(high[i] - low[i], 1e-9)
        body_ratio = abs(close[i] - open_[i]) / rng
        close_loc = (close[i] - low[i]) / rng
        bullish = close[i] >= open_[i]
        directional_weight = volume[i] * (0.5 * body_ratio + 0.5 * abs(close_loc - 0.5) * 2)
        directional_sum += directional_weight if bullish else -directional_weight
        weight_sum += volume[i]
    directional_bias = directional_sum / weight_sum if weight_sum > 0 else 0.0

    price_change_atr = (close[-1] - close[-1 - DIRECTIONAL_LOOKBACK]) / _atr if n > DIRECTIONAL_LOOKBACK else 0.0
    body_atr = _safe(abs(close[-1] - open_[-1]) / _atr)
    range_atr = _safe((high[-1] - low[-1]) / _atr)
    close_loc_last = (close[-1] - low[-1]) / max(high[-1] - low[-1], 1e-9)
    displacement_atr = abs(close[-1] - close[-2]) / _atr if n > 1 else 0.0
    efficiency = displacement_atr / max(_rvol / EFFICIENCY_RVOL_REF, 0.2)
    efficiency_label = "HIGH" if efficiency > 0.8 else ("LOW" if efficiency < 0.3 else "MODERATE")

    absorption = None
    if _rvol >= ABSORPTION_MIN_RVOL:
        upper_wick = high[-1] - max(close[-1], open_[-1])
        lower_wick = min(close[-1], open_[-1]) - low[-1]
        rng = max(high[-1] - low[-1], 1e-9)
        net_move_atr = (close[-1] - close[-2]) / _atr if n > 1 else 0.0
        close_loc_abs = (close[-1] - low[-1]) / rng
        if lower_wick / rng > 0.35 and net_move_atr > -ABSORPTION_MAX_DISPLACEMENT_ATR and close_loc_abs > 0.6:
            absorption = "BULLISH_ABSORPTION"
        elif upper_wick / rng > 0.35 and net_move_atr < ABSORPTION_MAX_DISPLACEMENT_ATR and close_loc_abs < 0.4:
            absorption = "BEARISH_ABSORPTION"

    streak = 0
    streak_dir = None
    for k in range(n - 1, max(n - 15, 0), -1):
        d = close[k] > open_[k]
        if streak_dir is None:
            streak_dir, streak = d, 1
        elif d == streak_dir:
            streak += 1
        else:
            break
    bodies = np.abs(close[-4:] - open_[-4:])
    body_declining = len(bodies) >= 3 and bodies[-1] < bodies[0] * 0.6
    exhaustion = None
    if streak >= EXHAUSTION_MIN_STREAK and vol_trend == "CONTRACTING" and body_declining:
        exhaustion = "BULLISH_EXHAUSTION" if streak_dir else "BEARISH_EXHAUSTION"

    # "new glasses" #4 (2026-09-13): divergence is independently
    # recomputed here (same exact formula as analyze()), mirroring the
    # existing absorption/exhaustion pattern above. Previously this
    # module only surfaced divergence when it happened to WIN
    # analyze()'s single-string state-priority race (absorption >
    # exhaustion > divergence > ...) -- if absorption or exhaustion
    # also fired on the same candle, a real divergence would be
    # silently invisible to every consumer of this observation. Now
    # it's its own flag/tag regardless of what state ends up winning.
    # Detect math in __init__.py::analyze() is untouched; this is a
    # read-only, additive recomputation of a formula that already
    # exists there.
    price_window = close[-DIVERGENCE_LOOKBACK:]
    dvol_series = []
    for i in range(n - DIVERGENCE_LOOKBACK, n):
        sign = 1 if close[i] >= open_[i] else -1
        dvol_series.append(sign * volume[i])
    dvol_series = np.array(dvol_series, dtype=float)
    divergence = None
    mid = len(price_window) // 2
    if mid > 2:
        recent_hi = int(np.argmax(price_window[mid:])) + mid
        early_hi = int(np.argmax(price_window[:mid]))
        recent_lo = int(np.argmin(price_window[mid:])) + mid
        early_lo = int(np.argmin(price_window[:mid]))
        if price_window[recent_hi] > price_window[early_hi] and dvol_series[recent_hi] < dvol_series[early_hi]:
            divergence = "VOLUME_DIVERGENCE_BEARISH"
        if divergence is None and price_window[recent_lo] < price_window[early_lo] and dvol_series[recent_lo] > dvol_series[early_lo]:
            divergence = "VOLUME_DIVERGENCE_BULLISH"

    recent_high = float(np.max(high[-BREAKOUT_LOOKBACK - 1:-1])) if n > BREAKOUT_LOOKBACK + 1 else float(high[-2])
    recent_low = float(np.min(low[-BREAKOUT_LOOKBACK - 1:-1])) if n > BREAKOUT_LOOKBACK + 1 else float(low[-2])
    breakout_context = None
    if close[-1] > recent_high:
        breakout_context = "BREAKOUT_VOLUME_STRONG" if (_rvol >= 1.3 and body_atr >= 0.5) else "BREAKOUT_VOLUME_WEAK"
    elif close[-1] < recent_low:
        breakout_context = "BREAKOUT_VOLUME_STRONG" if (_rvol >= 1.3 and body_atr >= 0.5) else "BREAKOUT_VOLUME_WEAK"

    followthrough = None
    if len(rvol_hist) >= 3:
        followthrough = "GOOD" if rvol_hist[-1] >= rvol_hist[0] * 0.75 else "WEAK"

    from . import analyze as _analyze
    ar = _analyze(candles, timeframe)
    state = "NEUTRAL"
    for line in ar.evidence:
        if line.startswith("State: "):
            state = line.split("State: ", 1)[1]
            break

    measurements = [
        Measurement("rvol20", float(_rvol), unit="ratio", origin="mib"),
        Measurement("directional_bias", float(directional_bias), unit="ratio", origin="mib"),
        Measurement("price_change_atr", float(price_change_atr), unit="ATR", origin="mib"),
        Measurement("impulse_body_atr", float(body_atr), unit="ATR", origin="mib"),
        Measurement("impulse_range_atr", float(range_atr), unit="ATR", origin="mib"),
        Measurement("close_location", float(close_loc_last), unit="ratio", origin="mib"),
        Measurement("efficiency", float(efficiency), unit="ratio", origin="mib"),
        Measurement("displacement_atr", float(displacement_atr), unit="ATR", origin="mib"),
        Measurement("rvol_trend_change", float(change), unit="ratio", origin="mib"),
        Measurement("streak", float(streak), unit="bars", origin="mib"),
        Measurement("confirmation_lag_bars", 0.0, unit="bars", origin="mib"),
    ]

    flags = {
        "closed_candle": True,
        "directional_bullish": bool(directional_bias > 0),
        "has_absorption": absorption is not None,
        "has_exhaustion": exhaustion is not None,
        "has_breakout_volume": breakout_context is not None,
        "has_divergence": divergence is not None,
    }

    tags = ["VOLUME", state, participation, vol_trend]
    if absorption:
        tags.append(absorption)
    if exhaustion:
        tags.append(exhaustion)
    if divergence:
        tags.append(divergence)
    if breakout_context:
        tags.append(breakout_context)
    if followthrough:
        tags.append(f"FOLLOWTHROUGH_{followthrough}")
    tags.append(f"EFFICIENCY_{efficiency_label}")

    notes = [
        f"RVOL {_rvol:.2f} — {participation} participation",
        f"Volume trend: {vol_trend}",
        f"Divergence: {divergence or 'none'}",
        f"State: {state}",
    ]

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="VOLUME",
        timeframe=timeframe,
        provenance=_prov(event_timestamp=last_ts, candle_timestamp=last_ts),
        state=state,
        measurements=measurements,
        flags=flags,
        tags=tags,
        notes=notes,
        valid=bool(ar.valid),
    )