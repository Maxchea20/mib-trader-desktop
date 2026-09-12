"""Step 6C — Structure / SMC AnalysisObservation producer.

Decision (three-tier):
  B — migrate existing MiB MarketState structure first.
  Represent LuxAlgo's extra tiers as tagged comparison evidence:
    * internal pivots via centralized find_pivots(left=5, right=5)
    * EQH/EQL via LuxAlgo formula abs(diff) < 0.1 * atr(200)
  Do not rewrite detection, OBs, FVG, MTF, or premium/discount.

analyze() and AgentResult stay untouched.
"""
from typing import Optional

import numpy as np

from ..indicators import arrays, atr as _atr, find_pivots
from ..market_state.builder import build_market_state, pivot_window_for_timeframe
from ..observation import (
    AnalysisEvent,
    AnalysisObservation,
    Measurement,
    ObservationLevel,
    Provenance,
)
from . import AGENT_ID

TV_INTERNAL_SIZE = 5
TV_SWING_SIZE = 50
TV_EQ_SIZE = 3
TV_EQ_THRESHOLD = 0.1
TV_ATR_PERIOD = 200


def _tv_atr200(high, low, close) -> float:
    period = min(TV_ATR_PERIOD, max(len(close) - 1, 1))
    return float(_atr(high, low, close, period))


def observe(
    candles,
    timeframe: str,
    market_state: Optional[object] = None,
    pivot_window_override: Optional[int] = None,
) -> AnalysisObservation:
    detection_ts = int(candles[-1]["ts"]) if candles else 0
    price_now = float(candles[-1]["close"]) if candles else None
    last_ts = detection_ts

    def _prov(**extra):
        kw = dict(
            source_module=AGENT_ID,
            timeframe=timeframe,
            detection_timestamp=detection_ts,
            source_calculation="market_state.builder.build_market_state",
            price_at_detection=price_now,
        )
        kw.update(extra)
        return Provenance(**kw)

    if len(candles) < 30:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="STRUCTURE",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=["Not enough candles"], valid=False,
        )

    try:
        state = (
            market_state
            if market_state is not None
            else build_market_state(
                candles,
                symbol="UNKNOWN",
                timeframe=timeframe,
                pivot_window_override=pivot_window_override,
            )
        )
    except Exception as exc:
        return AnalysisObservation(
            source=AGENT_ID, observation_type="STRUCTURE",
            timeframe=timeframe, provenance=_prov(),
            state="NONE", notes=[f"MarketState error: {exc}"], valid=False,
        )

    structure = state.structure
    a = arrays(candles)
    high, low, close = a["high"], a["low"], a["close"]
    ts = a["ts"]
    n = len(close)
    pivot_right = (
        pivot_window_override
        if pivot_window_override is not None
        else pivot_window_for_timeframe(timeframe)
    )

    event_ts = None
    if structure.event and structure.event.timestamp:
        event_ts = int(structure.event.timestamp)

    measurements = [
        Measurement("pivot_window", float(pivot_right), unit="bars", origin="mib"),
        Measurement("confirmation_lag_bars", float(pivot_right), unit="bars", origin="mib"),
        Measurement("atr", float(state.volatility.atr), unit="price", origin="mib"),
        Measurement("break_distance_atr", float(structure.break_distance_atr), unit="ATR", origin="mib"),
        Measurement("swing_high_count", float(len(state.swing_highs)), unit="count", origin="mib"),
        Measurement("swing_low_count", float(len(state.swing_lows)), unit="count", origin="mib"),
        Measurement("tv_internal_leg_size", float(TV_INTERNAL_SIZE), unit="bars", origin="tradingview_luxalgo"),
        Measurement("tv_swing_leg_size", float(TV_SWING_SIZE), unit="bars", origin="tradingview_luxalgo"),
        Measurement("tv_eq_leg_size", float(TV_EQ_SIZE), unit="bars", origin="tradingview_luxalgo"),
        Measurement("tv_eq_threshold", float(TV_EQ_THRESHOLD), unit="ratio", origin="tradingview_luxalgo"),
        Measurement("tv_atr_period", float(TV_ATR_PERIOD), unit="bars", origin="tradingview_luxalgo"),
    ]

    atr200 = _tv_atr200(high, low, close)
    measurements.append(Measurement("tv_atr200", float(atr200), unit="price", origin="tradingview_luxalgo"))

    internal = find_pivots(high, low, left=TV_INTERNAL_SIZE, right=TV_INTERNAL_SIZE)
    measurements.append(Measurement("tv_internal_pivot_count", float(len(internal)), unit="count", origin="tradingview_luxalgo"))

    eq_piv = find_pivots(high, low, left=TV_EQ_SIZE, right=TV_EQ_SIZE)
    eq_highs = [p for p in eq_piv if p["type"] == "H"]
    eq_lows = [p for p in eq_piv if p["type"] == "L"]
    eqh = eql = False
    eqh_diff = eql_diff = 0.0
    if len(eq_highs) >= 2 and atr200 > 0:
        eqh_diff = abs(eq_highs[-1]["price"] - eq_highs[-2]["price"])
        eqh = eqh_diff < TV_EQ_THRESHOLD * atr200
    if len(eq_lows) >= 2 and atr200 > 0:
        eql_diff = abs(eq_lows[-1]["price"] - eq_lows[-2]["price"])
        eql = eql_diff < TV_EQ_THRESHOLD * atr200
    measurements.append(Measurement("tv_eqh_price_diff", float(eqh_diff), unit="price", origin="tradingview_luxalgo"))
    measurements.append(Measurement("tv_eql_price_diff", float(eql_diff), unit="price", origin="tradingview_luxalgo"))

    levels = []
    if structure.last_high is not None:
        levels.append(ObservationLevel(
            label="Swing High", price=round(float(structure.last_high), 2),
            level_type="resistance", role="reference", timeframe=timeframe,
        ))
    if structure.last_low is not None:
        levels.append(ObservationLevel(
            label="Swing Low", price=round(float(structure.last_low), 2),
            level_type="support", role="reference", timeframe=timeframe,
        ))
    if structure.event and structure.event.reference_price:
        levels.append(ObservationLevel(
            label=f"{structure.event.event} reference",
            price=round(float(structure.event.reference_price), 2),
            level_type="bos" if structure.event.event == "BOS" else "reference",
            role="broken" if structure.event.event in ("BOS", "CHoCH") else "reference",
            timeframe=timeframe,
        ))

    ih = [p for p in internal if p["type"] == "H"]
    il = [p for p in internal if p["type"] == "L"]
    if ih:
        levels.append(ObservationLevel(
            label="Internal High", price=round(float(ih[-1]["price"]), 2),
            level_type="resistance", role="reference", timeframe=timeframe,
        ))
    if il:
        levels.append(ObservationLevel(
            label="Internal Low", price=round(float(il[-1]["price"]), 2),
            level_type="support", role="reference", timeframe=timeframe,
        ))
    if eqh and len(eq_highs) >= 2:
        levels.append(ObservationLevel(
            label="EQH", price=round(float(eq_highs[-1]["price"]), 2),
            level_type="eqh", role="equal", timeframe=timeframe,
        ))
    if eql and len(eq_lows) >= 2:
        levels.append(ObservationLevel(
            label="EQL", price=round(float(eq_lows[-1]["price"]), 2),
            level_type="eql", role="equal", timeframe=timeframe,
        ))

    if structure.event and structure.event.swing_index is not None:
        si = int(structure.event.swing_index)
        if 0 <= si < n:
            levels.append(ObservationLevel(
                label="Origin candle zone",
                price=round(float((float(high[si]) + float(low[si])) / 2.0), 2),
                level_type="order_block",
                role="origin",
                timeframe=timeframe,
                upper=round(float(high[si]), 2),
                lower=round(float(low[si]), 2),
            ))

    flags = {
        "closed_candle": True,
        "hh": bool(structure.hh),
        "hl": bool(structure.hl),
        "lh": bool(structure.lh),
        "ll": bool(structure.ll),
        "eqh": bool(eqh),
        "eql": bool(eql),
        "has_bos": structure.event.event == "BOS",
        "has_choch": structure.event.event == "CHoCH",
        "developing_high": bool(structure.developing_high),
        "developing_low": bool(structure.developing_low),
    }

    tags = ["SWING"]
    if internal:
        tags.append("INTERNAL")
    if eqh or eql:
        tags.append("EQUAL")
    if structure.event.event == "BOS":
        tags.append("BOS")
    if structure.event.event == "CHoCH":
        tags.append("CHOCH")
    if any(lv.level_type == "order_block" for lv in levels):
        tags.append("ORDER_BLOCK")

    history = []
    prior = "NEUTRAL"
    for ev in structure.events[-20:]:
        det = int(ev.timestamp) if ev.timestamp else last_ts
        history.append(AnalysisEvent(
            event_type=ev.event,
            direction="BULLISH" if ev.direction == "LONG" else ("BEARISH" if ev.direction == "SHORT" else "NEUTRAL"),
            timestamp=int(ev.timestamp) if ev.timestamp else last_ts,
            price=float(ev.price),
            reference_price=ev.reference_price,
            swing_index=ev.swing_index,
            distance_atr=float(ev.distance_atr or 0.0),
            source=AGENT_ID,
            detection_timestamp=det,
            prior_state={"LONG": "BULLISH", "SHORT": "BEARISH"}.get(prior, prior),
        ))
        if ev.direction == "LONG":
            prior = "BULLISH"
        elif ev.direction == "SHORT":
            prior = "BEARISH"

    if structure.hh and structure.last_high is not None:
        history.append(AnalysisEvent(
            event_type="HH", direction="BULLISH", timestamp=last_ts,
            price=float(structure.last_high), source=AGENT_ID, detection_timestamp=last_ts,
        ))
    if structure.hl and structure.last_low is not None:
        history.append(AnalysisEvent(
            event_type="HL", direction="BULLISH", timestamp=last_ts,
            price=float(structure.last_low), source=AGENT_ID, detection_timestamp=last_ts,
        ))
    if structure.lh and structure.last_high is not None:
        history.append(AnalysisEvent(
            event_type="LH", direction="BEARISH", timestamp=last_ts,
            price=float(structure.last_high), source=AGENT_ID, detection_timestamp=last_ts,
        ))
    if structure.ll and structure.last_low is not None:
        history.append(AnalysisEvent(
            event_type="LL", direction="BEARISH", timestamp=last_ts,
            price=float(structure.last_low), source=AGENT_ID, detection_timestamp=last_ts,
        ))
    if eqh and len(eq_highs) >= 2:
        i1 = int(eq_highs[-1]["i"])
        history.append(AnalysisEvent(
            event_type="EQH", direction="BEARISH",
            timestamp=int(ts[i1]),
            price=float(eq_highs[-1]["price"]),
            reference_price=float(eq_highs[-2]["price"]),
            swing_index=i1,
            source=AGENT_ID,
            detection_timestamp=int(ts[min(i1 + TV_EQ_SIZE, n - 1)]),
        ))
    if eql and len(eq_lows) >= 2:
        i1 = int(eq_lows[-1]["i"])
        history.append(AnalysisEvent(
            event_type="EQL", direction="BULLISH",
            timestamp=int(ts[i1]),
            price=float(eq_lows[-1]["price"]),
            reference_price=float(eq_lows[-2]["price"]),
            swing_index=i1,
            source=AGENT_ID,
            detection_timestamp=int(ts[min(i1 + TV_EQ_SIZE, n - 1)]),
        ))

    notes = [
        f"Sequence {structure.structure_sequence} | regime {structure.regime} | "
        f"event {structure.event.event}",
    ]

    obs_state = structure.event.event if structure.event.event != "NONE" else structure.regime

    return AnalysisObservation(
        source=AGENT_ID,
        observation_type="STRUCTURE",
        timeframe=timeframe,
        provenance=_prov(event_timestamp=event_ts, candle_timestamp=last_ts),
        state=obs_state,
        measurements=measurements,
        levels=levels,
        flags=flags,
        tags=tags,
        history=history,
        notes=notes,
        valid=True,
    )
