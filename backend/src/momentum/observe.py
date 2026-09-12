"""Step 6E — Momentum V2 AnalysisObservation producer.

Source of truth remains analyze() / Momentum V2.
TV mom0/mom1 are comparison measurements only.
"""
import numpy as np

from ..contract import LONG, NEUTRAL, SHORT
from ..indicators import arrays, atr, ema, macd, roc, rsi
from ..observation import (
    AnalysisEvent,
    AnalysisObservation,
    Measurement,
    Provenance,
)
from . import (
    ACCEL_LOOKBACK,
    AGENT_ID,
    DISPLACEMENT_ATR_CAP,
    DIVERGENCE_LOOKBACK,
    EXHAUSTION_MIN_STREAK,
    EXPANSION_LOOKBACK,
    IMPULSE_ATR_CAP,
    MIN_CANDLES,
    RETRACEMENT_LOOKBACK,
    _local_extremes,
    _macd_hist_series,
    _roc_series,
    _safe,
)


def _tv_mom0_mom1(close: np.ndarray):
    if len(close) < 14:
        return 0.0, 0.0
    mom0 = float(close[-1] - close[-13])
    mom0_prev = float(close[-2] - close[-14]) if len(close) >= 14 else 0.0
    return mom0, mom0 - mom0_prev


def observe(candles, timeframe: str) -> AnalysisObservation:
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None

    def _prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="momentum.analyze",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < MIN_CANDLES:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="MOMENTUM",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=["Not enough candles"], valid=False,
        )

    a = arrays(candles)
    high, low, close, open_ = a["high"], a["low"], a["close"], a["open"]
    n = len(close)
    last_ts = int(candles[-1]["ts"])

    _atr = atr(high, low, close, 14)
    if _atr <= 0 or not np.isfinite(_atr):
        return AnalysisObservation(
            source=AGENT_ID, observation_type="MOMENTUM",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["Invalid ATR"], valid=False,
        )

    _rsi = _safe(rsi(close, 14), 50.0)
    _, _, hist = macd(close)
    hist = _safe(hist)
    roc5 = _safe(roc(close, 5))
    roc12 = _safe(roc(close, 12))
    roc20 = _safe(roc(close, 20)) if n >= 21 else 0.0
    e20 = ema(close, 20)
    slope_atr = _safe((e20[-1] - e20[-5]) / _atr) if len(e20) >= 5 else 0.0
    displacement_atr = _safe((close[-1] - close[-6]) / _atr) if n >= 6 else 0.0

    roc_hist = _roc_series(close, 12, ACCEL_LOOKBACK + 2)
    if len(roc_hist) > ACCEL_LOOKBACK:
        accel_raw = roc_hist[-1] - roc_hist[-1 - ACCEL_LOOKBACK]
    else:
        accel_raw = 0.0
    accelerating = (accel_raw > 0) == (roc12 > 0) and abs(accel_raw) > 1e-9

    body_atr = _safe(abs(close[-1] - open_[-1]) / _atr)
    range_atr = _safe((high[-1] - low[-1]) / _atr)
    impulse_bullish = close[-1] > open_[-1]

    hist_series = _macd_hist_series(close, EXPANSION_LOOKBACK)
    if len(hist_series) >= 2:
        mag_series = np.abs(hist_series)
        expanding = mag_series[-1] > mag_series[0]
    else:
        expanding = False

    streak = 0
    streak_dir = None
    for k in range(n - 1, max(n - 15, 0), -1):
        d = close[k] > open_[k]
        if streak_dir is None:
            streak_dir = d
            streak = 1
        elif d == streak_dir:
            streak += 1
        else:
            break

    price_extending = False
    if n >= 9 and streak_dir is not None:
        if streak_dir:
            price_extending = close[-1] >= float(np.max(close[-8:-1]))
        else:
            price_extending = close[-1] <= float(np.min(close[-8:-1]))
    exhaustion_evidence = 0
    if streak >= EXHAUSTION_MIN_STREAK:
        exhaustion_evidence += 1
    if not expanding and len(hist_series) >= 2:
        exhaustion_evidence += 1
    if price_extending:
        exhaustion_evidence += 1
    if (streak_dir and _rsi > 65) or (streak_dir is False and _rsi < 35):
        exhaustion_evidence += 1
    if exhaustion_evidence >= 3:
        exhaustion_state = "HIGH"
    elif exhaustion_evidence == 2:
        exhaustion_state = "DEVELOPING"
    else:
        exhaustion_state = "LOW"
    exhausting = exhaustion_state == "HIGH"

    velocity_bullish = roc12 > 0
    mom0, mom1 = _tv_mom0_mom1(close)

    from . import analyze as _analyze
    ar = _analyze(candles, timeframe)
    state = "NEUTRAL"
    for line in ar.evidence:
        if line.startswith("State: "):
            state = line.split("State: ", 1)[1]
            break

    measurements = [
        Measurement("rsi14", float(_rsi), unit="index", origin="mib"),
        Measurement("roc5", float(roc5), unit="pct", origin="mib"),
        Measurement("roc12", float(roc12), unit="pct", origin="mib"),
        Measurement("roc20", float(roc20), unit="pct", origin="mib"),
        Measurement("macd_hist", float(hist), unit="price", origin="mib"),
        Measurement("ema20_slope_atr", float(slope_atr), unit="ATR", origin="mib"),
        Measurement("acceleration_roc12", float(accel_raw), unit="pct", origin="mib"),
        Measurement("velocity_roc12", float(roc12), unit="pct", origin="mib"),
        Measurement("displacement_atr", float(displacement_atr), unit="ATR", origin="mib"),
        Measurement("impulse_body_atr", float(body_atr), unit="ATR", origin="mib"),
        Measurement("impulse_range_atr", float(range_atr), unit="ATR", origin="mib"),
        Measurement("streak", float(streak), unit="bars", origin="mib"),
        Measurement("exhaustion_factors", float(exhaustion_evidence), unit="count", origin="mib"),
        Measurement("tv_mom0", float(mom0), unit="price", origin="tradingview_luxalgo"),
        Measurement("tv_mom1", float(mom1), unit="price", origin="tradingview_luxalgo"),
        Measurement("tv_mom_length", 12.0, unit="bars", origin="tradingview_luxalgo"),
    ]

    flags = {
        "closed_candle": True,
        "velocity_bullish": bool(velocity_bullish),
        "accelerating": bool(accelerating),
        "expanding": bool(expanding),
        "exhausting": bool(exhausting),
        "impulse_bullish": bool(impulse_bullish),
        "tv_mom_not_used_for_direction": True,
    }

    tags = ["MOMENTUM", state]
    if exhausting:
        tags.append("EXHAUSTING")
    if accelerating:
        tags.append("ACCELERATING")

    notes = [
        f"ROC5={roc5:+.2f}% ROC12={roc12:+.2f}% ROC20={roc20:+.2f}%",
        f"Exhaustion={exhaustion_state}",
        f"State: {state}",
    ]

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="MOMENTUM",
        timeframe=timeframe,
        provenance=_prov(event_timestamp=last_ts, candle_timestamp=last_ts),
        state=state,
        measurements=measurements,
        flags=flags,
        tags=tags,
        notes=notes,
        valid=bool(ar.valid),
    )
