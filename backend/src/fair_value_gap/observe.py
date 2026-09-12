"""Step 6D — Fair Value Gap AnalysisObservation producer.

analyze() keeps MiB 3-candle + MIN_FVG_PCT + MIN_DISPLACEMENT_ATR.
This module only publishes those facts, plus comparison measurements
for LuxAlgo extras that are NOT applied to detection:

- lastClose > last2High / lastClose < last2Low (middle-candle close)
- adaptive threshold: cum(abs(body_pct)) / n * 2  (causal, same-TF)
- request.security(..., lookahead_on) is NOT reproduced
"""
import numpy as np

from ..indicators import arrays
from ..observation import (
    AnalysisEvent,
    AnalysisObservation,
    Measurement,
    ObservationLevel,
    Provenance,
)
from . import AGENT_ID, MAX_FVGS, MIN_DISPLACEMENT_ATR, MIN_FVG_PCT, _atr, _mitigation_state


def _collect_fvgs(high, low, close, open_, atr, current_price, n):
    fvgs = []
    for i in range(2, n):
        high_2, low_2 = high[i - 2], low[i - 2]
        current_high, current_low = high[i], low[i]
        middle_open, middle_close = open_[i - 1], close[i - 1]
        middle_body = abs(middle_close - middle_open)

        if current_low > high_2:
            direction, zone_lo, zone_hi = "bull", float(high_2), float(current_low)
        elif current_high < low_2:
            direction, zone_lo, zone_hi = "bear", float(current_high), float(low_2)
        else:
            continue
        gap = zone_hi - zone_lo
        if gap <= 0:
            continue
        gap_pct = gap / current_price * 100
        gap_atr = gap / atr
        displacement_atr = middle_body / atr
        if gap_pct < MIN_FVG_PCT or displacement_atr < MIN_DISPLACEMENT_ATR:
            continue
        tv_middle_close = (
            (direction == "bull" and float(middle_close) > float(high_2))
            or (direction == "bear" and float(middle_close) < float(low_2))
        )
        fvg = {
            "dir": direction, "lo": zone_lo, "hi": zone_hi, "i": i,
            "size": gap, "size_pct": gap_pct, "gap_atr": gap_atr,
            "displacement_atr": displacement_atr,
            "tv_middle_close": tv_middle_close,
        }
        fvg["state"] = _mitigation_state(fvg, high, low, i + 1)
        fvg["mitigated"] = fvg["state"] != "fresh"
        fvgs.append(fvg)
    return fvgs[-MAX_FVGS:]


def _tv_adaptive_threshold(open_, close) -> float:
    """Causal same-TF analogue of LuxAlgo adaptive threshold."""
    n = len(close)
    if n < 2:
        return 0.0
    acc = 0.0
    last = 0.0
    for i in range(1, n):
        o = float(open_[i - 1])
        if abs(o) <= 1e-12:
            continue
        acc += abs((float(close[i - 1]) - o) / (o * 100.0))
        last = (acc / float(i)) * 2.0
    return float(last)


def observe(candles, timeframe: str) -> AnalysisObservation:
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None

    def _prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="fair_value_gap.analyze",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < 30:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="FAIR_VALUE_GAP",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=["Not enough candles"], valid=False,
        )

    a = arrays(candles)
    high = np.asarray(a["high"], dtype=float)
    low = np.asarray(a["low"], dtype=float)
    close = np.asarray(a["close"], dtype=float)
    open_ = np.asarray(a["open"], dtype=float)
    n = len(close)
    current_price = float(close[-1])
    last_ts = int(candles[-1]["ts"])

    atr = _atr(high, low, close, period=14)
    if atr <= 0:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="FAIR_VALUE_GAP",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", notes=["Invalid ATR"], valid=False,
        )

    fvgs = _collect_fvgs(high, low, close, open_, atr, current_price, n)
    tv_thr = _tv_adaptive_threshold(open_, close)
    mid_pass = sum(1 for f in fvgs if f["tv_middle_close"])

    measurements = [
        Measurement("atr", float(atr), unit="price", origin="mib"),
        Measurement("min_fvg_pct", float(MIN_FVG_PCT), unit="pct", origin="mib"),
        Measurement("min_displacement_atr", float(MIN_DISPLACEMENT_ATR), unit="ATR", origin="mib"),
        Measurement("fvg_count", float(len(fvgs)), unit="count", origin="mib"),
        Measurement("tv_adaptive_threshold", round(tv_thr, 8), unit="ratio", origin="tradingview_luxalgo"),
        Measurement("tv_middle_close_pass_count", float(mid_pass), unit="count", origin="tradingview_luxalgo"),
        Measurement("tv_lookahead_security_used", 0.0, unit="flag", origin="tradingview_luxalgo"),
    ]

    if not fvgs:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="FAIR_VALUE_GAP",
            timeframe=timeframe, provenance=_prov(candle_timestamp=last_ts),
            state="NONE", measurements=measurements,
            flags={"closed_candle": True},
            notes=["No significant FVGs"], valid=True,
        )

    fresh = [f for f in fvgs if f["state"] == "fresh"]
    partial = [f for f in fvgs if f["state"] == "partial"]
    filled = [f for f in fvgs if f["state"] == "filled"]
    active = fresh + partial
    nearest = min(active, key=lambda f: abs((f["lo"] + f["hi"]) / 2 - current_price)) if active else fvgs[-1]

    event_ts = int(candles[nearest["i"]]["ts"])
    measurements.extend([
        Measurement("gap_size", float(nearest["size"]), unit="price", origin="mib"),
        Measurement("gap_pct", float(nearest["size_pct"]), unit="pct", origin="mib"),
        Measurement("gap_atr", float(nearest["gap_atr"]), unit="ATR", origin="mib"),
        Measurement("displacement_atr", float(nearest["displacement_atr"]), unit="ATR", origin="mib"),
        Measurement("confirmation_lag_bars", 0.0, unit="bars", origin="mib"),
    ])

    levels = []
    for f in (active or fvgs)[-6:]:
        mid = (f["lo"] + f["hi"]) / 2
        levels.append(ObservationLevel(
            label="FVG bull" if f["dir"] == "bull" else "FVG bear",
            price=round(mid, 2),
            level_type="fvg_upper" if f["dir"] == "bear" else "fvg_lower",
            role="origin" if f is nearest else "reference",
            timeframe=timeframe,
            upper=round(f["hi"], 2),
            lower=round(f["lo"], 2),
        ))

    flags = {
        "closed_candle": True,
        "has_fresh": bool(fresh),
        "has_partial": bool(partial),
        "has_filled": bool(filled),
        "bullish": nearest["dir"] == "bull",
        "bearish": nearest["dir"] == "bear",
        "tv_middle_close": bool(nearest["tv_middle_close"]),
        "tv_lookahead_reproduced": False,
    }

    tags = ["FVG", nearest["dir"].upper(), nearest["state"].upper()]
    history = []
    for f in fvgs[-10:]:
        cts = int(candles[f["i"]]["ts"])
        history.append(AnalysisEvent(
            event_type="FVG_CREATED",
            direction="BULLISH" if f["dir"] == "bull" else "BEARISH",
            timestamp=cts,
            price=float((f["lo"] + f["hi"]) / 2),
            reference_price=f["hi"] if f["dir"] == "bull" else f["lo"],
            swing_index=f["i"],
            distance_atr=float(f["gap_atr"]),
            source=AGENT_ID,
            detection_timestamp=cts,
        ))
        if f["state"] == "filled":
            history.append(AnalysisEvent(
                event_type="FVG_FILLED",
                direction="BULLISH" if f["dir"] == "bull" else "BEARISH",
                timestamp=last_ts,
                price=current_price,
                reference_price=f["lo"] if f["dir"] == "bull" else f["hi"],
                source=AGENT_ID,
                detection_timestamp=last_ts,
            ))

    state = nearest["state"].upper()
    notes = [f"{len(fvgs)} significant FVGs ({len(fresh)} fresh, {len(partial)} partial)"]
    notes.append(f"{nearest['dir']} {nearest['lo']:.1f}–{nearest['hi']:.1f} state={nearest['state']}")

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="FAIR_VALUE_GAP",
        timeframe=timeframe,
        provenance=_prov(event_timestamp=event_ts, candle_timestamp=last_ts),
        state=state,
        measurements=measurements,
        levels=levels,
        flags=flags,
        tags=tags,
        history=history,
        notes=notes,
        valid=True,
    )
