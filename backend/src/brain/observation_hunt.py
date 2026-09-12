"""Observation Brain: 15m story + M5 fill at the level.

Watchers (observe()) report facts. This module decides the click.
Does not change agent detect math. Does not count votes.

Rules (tested 30d PF ~1.36 / 90d PF ~1.33):
- Arm on 15m BREAKOUT_DETECTED / BOS / CHoCH only (not FVG_CREATED).
- HTF 4h Trend opposing the side blocks unless the 15m event is CHoCH.
- Volume contradiction blocks.
- M5 must tag the 15m level band (0.25 * 15m ATR) and close on-side
  no more than 0.25 ATR through the level.
- Close through the band = late. Kill the arm. Do not chase.
- Fill price is the 15m level, not the M5 close.
- Stop 1.5 * 15m ATR, target 2.5 * 15m ATR.
- FVG / S/R are location only.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..breakout.observe import observe as obs_breakout
from ..fair_value_gap.observe import observe as obs_fvg
from ..indicators import arrays, atr
from ..structure.observe import observe as obs_structure
from ..support_resistance.observe import observe as obs_sr
from ..trend.observe import observe as obs_trend
from ..volume.observe import observe as obs_vol
from ..contract import LONG, SHORT, NEUTRAL, STATE_LONG, STATE_SHORT, STATE_WAIT

BAND = 0.25
SL_ATR = 1.5
TP_ATR = 2.5
ARM_MAX_15M = 4
HUNT_VERSION = "OBSERVATION_HUNT_M5_V1"


def _dir(d):
    d = (d or "").upper()
    if d in ("BULLISH", "LONG", "UP"):
        return LONG
    if d in ("BEARISH", "SHORT", "DOWN"):
        return SHORT
    return None


def _fresh(obs, bar_ts):
    out = []
    for e in obs.history or []:
        ts = e.timestamp or getattr(e, "detection_timestamp", None)
        if ts is not None and int(ts) == int(bar_ts):
            out.append(e)
    return out


def _trend_side(state):
    s = (state or "").upper()
    if "BULL" in s:
        return LONG
    if "BEAR" in s:
        return SHORT
    return None


def _vol_bad(o, side):
    st = (o.state or "").upper()
    if side == LONG and any(x in st for x in ("BEARISH_ABSORPTION", "BULLISH_EXHAUSTION", "VOLUME_DIVERGENCE_BEARISH")):
        return True
    if side == SHORT and any(x in st for x in ("BULLISH_ABSORPTION", "BEARISH_EXHAUSTION", "VOLUME_DIVERGENCE_BULLISH")):
        return True
    return False


def _level(sr, fv, side, price, atr_v):
    cands = []
    for lv in list(sr.levels or []) + list(fv.levels or []):
        if lv.price is None:
            continue
        px = float(lv.price)
        if atr_v > 0 and abs(px - price) <= 1.0 * atr_v:
            cands.append(px)
    return min(cands, key=lambda x: abs(x - price)) if cands else price


def _wait(why: str, extra: Optional[Dict] = None) -> Dict:
    out = {
        "action": "WAIT",
        "state": STATE_WAIT,
        "direction": NEUTRAL,
        "entry_readiness": False,
        "why_state": [why],
        "blocking_reasons": [why],
        "brain_version": HUNT_VERSION,
        "hunt": {"armed": False},
    }
    if extra:
        out.update(extra)
    return out


def evaluate_hunt(
    candles_15m: List[dict],
    candle_5m: dict,
    candles_4h: Optional[List[dict]] = None,
    atr_15m: Optional[float] = None,
) -> Dict:
    """One-shot: arm from last closed 15m, hunt on this closed 5m bar."""
    if not candles_15m or len(candles_15m) < 60 or not candle_5m:
        return _wait("not enough 15m/5m candles")

    bar15 = candles_15m[-1]
    p15 = float(bar15["close"])
    aa = arrays(candles_15m)
    atr15 = float(atr_15m if atr_15m is not None else (atr(aa["high"], aa["low"], aa["close"], 14) or 0.0))
    if atr15 <= 0:
        return _wait("invalid 15m ATR")

    br = obs_breakout(candles_15m, "15m")
    st = obs_structure(candles_15m, "15m")
    fv = obs_fvg(candles_15m, "15m")
    sr = obs_sr(candles_15m, "15m")
    vo = obs_vol(candles_15m, "15m")
    htf = None
    if candles_4h and len(candles_4h) >= 120:
        htf = obs_trend(candles_4h[-200:], "4h")

    trigs = []
    for e in _fresh(br, bar15["ts"]):
        if e.event_type == "BREAKOUT_DETECTED":
            s = _dir(e.direction)
            if s:
                trigs.append(("breakout", s, e))
    for e in _fresh(st, bar15["ts"]):
        if e.event_type in ("BOS", "CHoCH", "CHOCH"):
            s = _dir(e.direction)
            if s:
                trigs.append(("structure", s, e))

    if not trigs:
        return _wait("no fresh 15m BOS/CHoCH/breakout on this closed 15m")
    sides = {t[1] for t in trigs}
    if len(sides) != 1:
        return _wait("conflicting 15m triggers")
    side = next(iter(sides))
    primary = trigs[0]

    htf_side = _trend_side(htf.state) if htf else None
    choch = any(t[2].event_type in ("CHoCH", "CHOCH") for t in trigs)
    if htf_side and htf_side != side and not choch:
        return _wait("4h trend opposes 15m trigger")
    if _vol_bad(vo, side):
        return _wait("volume contradicts 15m trigger")

    level = _level(sr, fv, side, p15, atr15)
    band = BAND * atr15
    tagged = (side == LONG and candle_5m["low"] <= level + band) or (
        side == SHORT and candle_5m["high"] >= level - band
    )
    through = (candle_5m["close"] - level) if side == LONG else (level - candle_5m["close"])
    if tagged and through > band:
        return _wait("late M5 close — chased through the level", extra={
            "hunt": {"armed": True, "late": True, "level": level, "side": side}
        })
    on_side = (side == LONG and candle_5m["close"] >= level) or (
        side == SHORT and candle_5m["close"] <= level
    )
    near = abs(candle_5m["close"] - level) <= band
    if not (tagged and on_side and near):
        return _wait("M5 has not held the 15m level", extra={
            "hunt": {"armed": True, "level": level, "side": side, "atr15": atr15}
        })

    entry = float(level)
    if side == LONG:
        sl, tp = entry - SL_ATR * atr15, entry + TP_ATR * atr15
        state = STATE_LONG
    else:
        sl, tp = entry + SL_ATR * atr15, entry - TP_ATR * atr15
        state = STATE_SHORT
    size = "FULL" if htf_side == side else "HALF"
    return {
        "action": "FIRE",
        "state": state,
        "direction": side,
        "entry_readiness": True,
        "entry": entry,
        "stop": sl,
        "target": tp,
        "atr_15m": atr15,
        "size": size,
        "why_state": [
            f"15m {primary[0]} {primary[2].event_type}",
            f"M5 held level {entry:.1f}",
            "fill at 15m level, not M5 close",
        ],
        "blocking_reasons": [],
        "brain_version": HUNT_VERSION,
        "primary": primary[0],
        "event": primary[2].event_type,
        "htf_trend": htf.state if htf else None,
        "volume_state": vo.state,
        "hunt": {"armed": True, "level": level, "late": False},
    }
