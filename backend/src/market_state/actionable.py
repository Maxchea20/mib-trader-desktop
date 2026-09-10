"""Actionable structure layer on confirmed BOS/CHoCH levels.

Does not change the W=5 confirmed swing book.
Uses only the last closed bar plus already-confirmed reference_price.
"""
from __future__ import annotations

ACTIONABLE_PULLBACK_ATR = 0.5
ACTIONABLE_RETEST_ATR = 0.3


def apply_actionable_structure(structure, candles, atr: float, m5_candles=None, timeframe: str = "15m") -> None:
    ev = getattr(structure, "event", None)
    if ev is None or ev.event not in ("BOS", "CHoCH") or ev.reference_price is None:
        structure.actionable_state = "NONE"
        structure.m5_confirm = "NONE"
        return

    level = float(ev.reference_price)
    direction = ev.direction
    last = candles[-1]
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    dist = abs(close - level) / atr if atr > 0 else 0.0
    structure.actionable_level = level
    structure.actionable_distance_atr = round(dist, 3)

    if direction == "LONG":
        through = close < level
        toward = (low <= level + ACTIONABLE_PULLBACK_ATR * atr) if atr > 0 else False
        in_zone = (low <= level + ACTIONABLE_RETEST_ATR * atr) if atr > 0 else False
        held = close > level
    elif direction == "SHORT":
        through = close > level
        toward = (high >= level - ACTIONABLE_PULLBACK_ATR * atr) if atr > 0 else False
        in_zone = (high >= level - ACTIONABLE_RETEST_ATR * atr) if atr > 0 else False
        held = close < level
    else:
        structure.actionable_state = "NONE"
        return

    if ev.event == "CHoCH" and through:
        state = "REVERSAL"
    elif through:
        state = "FAILURE"
    elif in_zone and held:
        state = "RETEST" if dist <= ACTIONABLE_RETEST_ATR else "LEVEL_HOLD"
    elif toward and held:
        state = "PULLBACK"
    elif held:
        state = "CONTINUATION"
    else:
        state = "FAILURE"
    structure.actionable_state = state

    if len(candles) >= 5:
        window = candles[-5:]
        structure.developing_high = high >= max(float(c["high"]) for c in window)
        structure.developing_low = low <= min(float(c["low"]) for c in window)

    structure.m5_confirm = "NONE"
    if not m5_candles or atr <= 0:
        return
    last_ts = int(last["ts"])
    horizon = last_ts + 900 if timeframe == "15m" else last_ts + 300
    usable = [
        c for c in m5_candles
        if last_ts <= int(c["ts"]) < horizon and int(c["ts"]) + 300 <= horizon
    ]
    if not usable:
        return
    mc = float(usable[-1]["close"])
    if direction == "LONG":
        structure.m5_confirm = "FAIL" if mc < level else "HOLD"
    else:
        structure.m5_confirm = "FAIL" if mc > level else "HOLD"
