"""Step 6F — Trend V2 AnalysisObservation producer.

analyze() remains the source of truth. This adapter publishes the same
EMA-ribbon facts without votes.
"""
from ..indicators import arrays, atr, ema
from ..observation import (
    AnalysisObservation,
    Measurement,
    ObservationLevel,
    Provenance,
)
from . import AGENT_ID, MIN_CANDLES, PERSISTENCE_LOOKBACK, SLOPE_LOOKBACK


def observe(candles, timeframe: str) -> AnalysisObservation:
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None

    def _prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="trend.analyze",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < MIN_CANDLES:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="TREND",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=["Not enough candles"], valid=False,
        )

    a = arrays(candles)
    high, low, close = a["high"], a["low"], a["close"]
    last_ts = int(candles[-1]["ts"])
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    e100 = ema(close, 100)
    e20_now, e50_now, e100_now = float(e20[-1]), float(e50[-1]), float(e100[-1])
    price = float(close[-1])
    _atr = atr(high, low, close, 14)
    if _atr <= 0:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="TREND",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["Invalid ATR"], valid=False,
        )

    bull_align = e20_now > e50_now > e100_now
    bear_align = e20_now < e50_now < e100_now
    ema20_50_sep_atr = abs(e20_now - e50_now) / _atr
    ema50_100_sep_atr = abs(e50_now - e100_now) / _atr

    if len(close) > SLOPE_LOOKBACK:
        slope20 = (e20_now - float(e20[-SLOPE_LOOKBACK - 1])) / _atr
        slope50 = (e50_now - float(e50[-SLOPE_LOOKBACK - 1])) / _atr
        slope100 = (e100_now - float(e100[-SLOPE_LOOKBACK - 1])) / _atr
        p20 = float(e20[-SLOPE_LOOKBACK - 1])
        p50 = float(e50[-SLOPE_LOOKBACK - 1])
        p100 = float(e100[-SLOPE_LOOKBACK - 1])
        prev_width_atr = (max(p20, p50, p100) - min(p20, p50, p100)) / _atr
    else:
        slope20 = slope50 = slope100 = 0.0
        prev_width_atr = 0.0

    ribbon_hi = max(e20_now, e50_now, e100_now)
    ribbon_lo = min(e20_now, e50_now, e100_now)
    if price > e20_now > e50_now > e100_now:
        location_state = "ABOVE_RIBBON"
    elif price < e20_now < e50_now < e100_now:
        location_state = "BELOW_RIBBON"
    elif ribbon_lo <= price <= ribbon_hi:
        location_state = "INSIDE_RIBBON"
    else:
        location_state = "CROSSING_RIBBON"

    ribbon_width_atr = (ribbon_hi - ribbon_lo) / _atr
    width_change = ribbon_width_atr - prev_width_atr
    if width_change > 0.05:
        ribbon_state = "EXPANDING"
    elif width_change < -0.05:
        ribbon_state = "CONTRACTING"
    else:
        ribbon_state = "STABLE"

    lookback_n = min(PERSISTENCE_LOOKBACK, len(close) - 1, len(e50) - 1)
    if lookback_n > 0:
        bull_persistence = sum(1 for i in range(-lookback_n, 0) if close[i] > e50[i]) / lookback_n
        bear_persistence = sum(1 for i in range(-lookback_n, 0) if close[i] < e50[i]) / lookback_n
    else:
        bull_persistence = bear_persistence = 0.0

    from . import analyze as _analyze
    ar = _analyze(candles, timeframe)
    state = "NEUTRAL"
    for line in ar.evidence:
        if line.startswith("Trend state: "):
            state = line.split("Trend state: ", 1)[1]
            break

    measurements = [
        Measurement("ema20", e20_now, unit="price", origin="mib"),
        Measurement("ema50", e50_now, unit="price", origin="mib"),
        Measurement("ema100", e100_now, unit="price", origin="mib"),
        Measurement("ema20_50_sep_atr", float(ema20_50_sep_atr), unit="ATR", origin="mib"),
        Measurement("ema50_100_sep_atr", float(ema50_100_sep_atr), unit="ATR", origin="mib"),
        Measurement("slope20_atr", float(slope20), unit="ATR", origin="mib"),
        Measurement("slope50_atr", float(slope50), unit="ATR", origin="mib"),
        Measurement("slope100_atr", float(slope100), unit="ATR", origin="mib"),
        Measurement("ribbon_width_atr", float(ribbon_width_atr), unit="ATR", origin="mib"),
        Measurement("ribbon_width_change_atr", float(width_change), unit="ATR", origin="mib"),
        Measurement("bull_persistence", float(bull_persistence), unit="ratio", origin="mib"),
        Measurement("bear_persistence", float(bear_persistence), unit="ratio", origin="mib"),
        Measurement("persistence_lookback", float(lookback_n), unit="bars", origin="mib"),
        Measurement("confirmation_lag_bars", 0.0, unit="bars", origin="mib"),
    ]

    levels = [
        ObservationLevel(label="EMA20", price=round(e20_now, 2), level_type="dynamic", role="reference", timeframe=timeframe),
        ObservationLevel(label="EMA50", price=round(e50_now, 2), level_type="dynamic", role="reference", timeframe=timeframe),
        ObservationLevel(label="EMA100", price=round(e100_now, 2), level_type="dynamic", role="reference", timeframe=timeframe),
    ]

    flags = {
        "closed_candle": True,
        "bull_align": bool(bull_align),
        "bear_align": bool(bear_align),
        "mixed_align": bool(not bull_align and not bear_align),
    }

    tags = ["TREND", state, location_state, ribbon_state]
    if bull_align:
        tags.append("BULL_ALIGN")
    if bear_align:
        tags.append("BEAR_ALIGN")

    notes = [
        f"EMA alignment: {'BULLISH' if bull_align else ('BEARISH' if bear_align else 'MIXED')}",
        f"Price location: {location_state}",
        f"Ribbon state: {ribbon_state}",
        f"Trend state: {state}",
    ]

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="TREND",
        timeframe=timeframe,
        provenance=_prov(event_timestamp=last_ts, candle_timestamp=last_ts),
        state=state,
        measurements=measurements,
        levels=levels,
        flags=flags,
        tags=tags,
        notes=notes,
        valid=bool(ar.valid),
    )
