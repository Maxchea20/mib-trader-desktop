"""AGENT 5 — VOLUME V2."""
import numpy as np
from ..contract import AgentResult, LONG, SHORT, NEUTRAL, neutral, clamp
from ..indicators import arrays, atr, rvol

AGENT_ID = "volume"

MIN_CANDLES = 45
EXPANSION_LOOKBACK = 5
DIRECTIONAL_LOOKBACK = 10
IMPULSE_ATR_CAP = 1.5
EFFICIENCY_RVOL_REF = 1.5
ABSORPTION_MIN_RVOL = 1.8
ABSORPTION_MAX_DISPLACEMENT_ATR = 0.35
EXHAUSTION_MIN_STREAK = 3
BREAKOUT_LOOKBACK = 20
DIVERGENCE_LOOKBACK = 20

W_PARTICIPATION = 15.0
W_DIRECTIONAL = 15.0
W_CONFIRMATION = 20.0
W_IMPULSE = 15.0
W_EFFICIENCY = 10.0
W_EXPANSION = 10.0
W_ABSORPTION_EXHAUSTION = 5.0
W_DIVERGENCE = 5.0
W_BREAKOUT_FOLLOWTHROUGH = 5.0


def _safe(x, default=0.0):
    return default if (x is None or not np.isfinite(x)) else float(x)


def _rvol_series(volume, period, count):
    n = len(volume)
    out = []
    for end in range(max(period + 2, n - count), n + 1):
        out.append(rvol(volume[:end], period))
    return np.array(out, dtype=float)


def _participation_label(rv):
    if rv < 0.5:
        return "VERY_LOW"
    if rv < 0.85:
        return "LOW"
    if rv < 1.4:
        return "NORMAL"
    if rv < 2.2:
        return "HIGH"
    return "VERY_HIGH"


def _local_extremes(values, lookback):
    n = len(values)
    if n < 5:
        return None, None
    window = values[-lookback:]
    return (n - len(window) + int(np.argmax(window))), (n - len(window) + int(np.argmin(window)))


def analyze(candles, timeframe: str) -> AgentResult:
    if len(candles) < MIN_CANDLES:
        r = neutral(AGENT_ID, timeframe, "Not enough candles for Volume V2 analysis")
        r.valid = False
        return r

    a = arrays(candles)
    high, low, close, open_, volume = a["high"], a["low"], a["close"], a["open"], a["volume"]
    n = len(close)

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        r = neutral(AGENT_ID, timeframe, "Invalid ATR")
        r.valid = False
        return r
    if float(np.sum(volume[-20:])) <= 0:
        r = neutral(AGENT_ID, timeframe, "No volume data available")
        r.valid = False
        return r

    _rvol = _safe(rvol(volume, 20), 1.0)
    participation = _participation_label(_rvol)
    participation_score = clamp(abs(_rvol - 1.0) / 1.2, 0, 1) * W_PARTICIPATION

    rvol_hist = _rvol_series(volume, 20, EXPANSION_LOOKBACK)
    if len(rvol_hist) >= 4:
        half = len(rvol_hist) // 2
        early_mean = float(np.mean(rvol_hist[:half]))
        recent_mean = float(np.mean(rvol_hist[half:]))
        change = recent_mean - early_mean
        if change > 0.15:
            vol_trend = "EXPANDING"
        elif change < -0.15:
            vol_trend = "CONTRACTING"
        else:
            vol_trend = "STABLE"
    else:
        vol_trend, change = "STABLE", 0.0
    expansion_score = clamp(abs(change) / 0.6, 0, 1) * W_EXPANSION

    directional_sum = 0.0
    weight_sum = 0.0
    for i in range(n - DIRECTIONAL_LOOKBACK, n):
        rng = max(high[i] - low[i], 1e-9)
        body_ratio = abs(close[i] - open_[i]) / rng
        close_loc = (close[i] - low[i]) / rng
        bullish = close[i] >= open_[i]
        directional_weight = volume[i] * (0.5 * body_ratio + 0.5 * abs(close_loc - 0.5) * 2)
        directional_sum += directional_weight if bullish else -directional_weight
        weight_sum += volume[i]
    directional_bias = directional_sum / weight_sum if weight_sum > 0 else 0.0
    directional_score = clamp(abs(directional_bias) / 0.5, 0, 1) * W_DIRECTIONAL
    directional_bullish = directional_bias > 0

    price_change_atr = (close[-1] - close[-1 - DIRECTIONAL_LOOKBACK]) / _atr if n > DIRECTIONAL_LOOKBACK else 0.0
    price_bullish = price_change_atr > 0
    if vol_trend == "EXPANDING" and price_bullish == directional_bullish and abs(price_change_atr) > 0.15:
        confirmation = "bullish_confirmation" if price_bullish else "bearish_confirmation"
        confirmation_score = W_CONFIRMATION
    elif vol_trend == "CONTRACTING" and abs(price_change_atr) > 0.15:
        confirmation = "weak_bullish_confirmation" if price_bullish else "weak_bearish_confirmation"
        confirmation_score = W_CONFIRMATION * 0.35
    else:
        confirmation = "none"
        confirmation_score = W_CONFIRMATION * 0.15

    body_atr = _safe(abs(close[-1] - open_[-1]) / _atr)
    range_atr = _safe((high[-1] - low[-1]) / _atr)
    close_loc_last = (close[-1] - low[-1]) / max(high[-1] - low[-1], 1e-9)
    impulse_raw = clamp(_rvol / 2.5, 0, 1) * 0.4 + clamp(body_atr / IMPULSE_ATR_CAP, 0, 1) * 0.35 \
        + clamp(abs(close_loc_last - 0.5) * 2, 0, 1) * 0.25
    impulse_score = impulse_raw * W_IMPULSE

    displacement_atr = abs(close[-1] - close[-2]) / _atr if n > 1 else 0.0
    efficiency = displacement_atr / max(_rvol / EFFICIENCY_RVOL_REF, 0.2)
    efficiency_label = "HIGH" if efficiency > 0.8 else ("LOW" if efficiency < 0.3 else "MODERATE")
    efficiency_score = clamp(efficiency / 1.2, 0, 1) * W_EFFICIENCY

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

    absorption_exhaustion_score = W_ABSORPTION_EXHAUSTION if (absorption or exhaustion) else 0.0

    recent_high = float(np.max(high[-BREAKOUT_LOOKBACK - 1:-1]))
    recent_low = float(np.min(low[-BREAKOUT_LOOKBACK - 1:-1]))
    breakout_context = None
    if close[-1] > recent_high:
        breakout_context = "BREAKOUT_VOLUME_STRONG" if (_rvol >= 1.3 and body_atr >= 0.5) else "BREAKOUT_VOLUME_WEAK"
    elif close[-1] < recent_low:
        breakout_context = "BREAKOUT_VOLUME_STRONG" if (_rvol >= 1.3 and body_atr >= 0.5) else "BREAKOUT_VOLUME_WEAK"

    followthrough = None
    if len(rvol_hist) >= 3:
        followthrough = "GOOD" if rvol_hist[-1] >= rvol_hist[0] * 0.75 else "WEAK"
    breakout_followthrough_score = 0.0
    if breakout_context == "BREAKOUT_VOLUME_STRONG":
        breakout_followthrough_score += W_BREAKOUT_FOLLOWTHROUGH * 0.6
    if followthrough == "GOOD":
        breakout_followthrough_score += W_BREAKOUT_FOLLOWTHROUGH * 0.4

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
            divergence = "bearish_volume_divergence"
        if divergence is None and price_window[recent_lo] < price_window[early_lo] and dvol_series[recent_lo] > dvol_series[early_lo]:
            divergence = "bullish_volume_divergence"
    divergence_score = W_DIVERGENCE if divergence else 0.0

    total_score = (participation_score + directional_score + confirmation_score + impulse_score
                  + efficiency_score + expansion_score + absorption_exhaustion_score
                  + divergence_score + breakout_followthrough_score)
    confidence = clamp(25.0 + total_score * 0.65, 0, 94)
    strength = clamp(
        participation_score + directional_score + impulse_score + efficiency_score,
        0, W_PARTICIPATION + W_DIRECTIONAL + W_IMPULSE + W_EFFICIENCY,
    ) / (W_PARTICIPATION + W_DIRECTIONAL + W_IMPULSE + W_EFFICIENCY) * 100.0

    if absorption and abs(directional_bias) < 0.35:
        direction = NEUTRAL
    elif abs(directional_bias) < 0.12 or confirmation == "none":
        direction = NEUTRAL
    elif directional_bullish and confirmation in ("bullish_confirmation", "weak_bullish_confirmation"):
        direction = LONG
    elif not directional_bullish and confirmation in ("bearish_confirmation", "weak_bearish_confirmation"):
        direction = SHORT
    else:
        direction = NEUTRAL

    if vol_trend == "CONTRACTING" and direction != NEUTRAL:
        confidence *= 0.85

    if absorption:
        state = absorption
    elif exhaustion:
        state = exhaustion
    elif divergence == "bearish_volume_divergence":
        state = "VOLUME_DIVERGENCE_BEARISH"
    elif divergence == "bullish_volume_divergence":
        state = "VOLUME_DIVERGENCE_BULLISH"
    elif breakout_context:
        state = breakout_context
    elif confirmation == "bullish_confirmation":
        state = "BULLISH_CONFIRMATION"
    elif confirmation == "bearish_confirmation":
        state = "BEARISH_CONFIRMATION"
    elif vol_trend == "EXPANDING":
        state = "VOLUME_EXPANDING"
    elif vol_trend == "CONTRACTING":
        state = "VOLUME_CONTRACTING"
    elif direction == LONG:
        state = "STRONG_BULLISH_PARTICIPATION" if strength > 60 else "BULLISH_PARTICIPATION"
    elif direction == SHORT:
        state = "STRONG_BEARISH_PARTICIPATION" if strength > 60 else "BEARISH_PARTICIPATION"
    else:
        state = "NEUTRAL"

    evidence = [
        f"RVOL {_rvol:.2f} — {participation} participation",
        f"Directional volume: {'bullish' if directional_bullish else 'bearish'} bias {abs(directional_bias)*100:.0f}%",
        f"Price displacement {price_change_atr:+.2f} ATR over {DIRECTIONAL_LOOKBACK} bars, volume {vol_trend.lower()}",
        f"Price-volume confirmation: {confirmation.replace('_', ' ')}",
        f"Volume impulse: body {body_atr:.2f} ATR, close location {close_loc_last:.0%} ({impulse_score:.1f}/{W_IMPULSE:.0f})",
        f"Volume efficiency: {efficiency_label.lower()} ({displacement_atr:.2f} ATR displacement per unit participation)",
        f"Absorption: {absorption or 'none'}",
        f"Exhaustion: {exhaustion or 'none'} ({streak} consecutive {'bullish' if streak_dir else 'bearish'} candle(s))",
        f"Breakout volume context: {breakout_context or 'none'}" + (f", follow-through {followthrough.lower()}" if breakout_context and followthrough else ""),
        f"Divergence: {(divergence or 'none').replace('_', ' ')}",
        f"State: {state}",
    ]

    valid = strength >= 12.0 or direction == NEUTRAL

    return AgentResult(AGENT_ID, direction, round(confidence, 1), round(strength, 1),
                       evidence, [], timeframe, valid=valid)


from .observe import observe  # Step 6G — AnalysisObservation producer
