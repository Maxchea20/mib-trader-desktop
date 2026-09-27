"""Closed-candle weather: CHOP / SWING_UP / SWING_DOWN.\n\nCausal 4h (+ optional 1h veto). No calendar fitting.\n"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

CHOP, SWING_UP, SWING_DOWN, UNKNOWN = "CHOP", "SWING_UP", "SWING_DOWN", "UNKNOWN"
WEATHER_VERSION = "WEATHER_V1"


def _atr(rows: List[dict], n: int = 14) -> Optional[float]:
    if len(rows) < n + 1:
        return None
    w = rows[-(n + 1):]
    prev = None
    acc = 0.0
    k = 0
    for b in w[1:]:
        tr = float(b["high"]) - float(b["low"])
        if prev is not None:
            tr = max(tr, abs(float(b["high"]) - prev), abs(float(b["low"]) - prev))
        acc += tr
        k += 1
        prev = float(b["close"])
    return acc / k if k else None


def _close_votes(rows: List[dict], n: int = 6):
    if len(rows) < n + 1:
        return 0, 0
    up = down = 0
    for a, b in zip(rows[-(n + 1):-1], rows[-n:]):
        if float(b["close"]) > float(a["close"]):
            up += 1
        elif float(b["close"]) < float(a["close"]):
            down += 1
    return up, down


def _body_vote(bar: dict) -> Optional[str]:
    rng = float(bar["high"]) - float(bar["low"])
    if rng <= 0:
        return None
    if abs(float(bar["close"]) - float(bar["open"])) / rng < 0.50:
        return None
    third = rng / 3.0
    if float(bar["close"]) >= float(bar["high"]) - third and float(bar["close"]) > float(bar["open"]):
        return SWING_UP
    if float(bar["close"]) <= float(bar["low"]) + third and float(bar["close"]) < float(bar["open"]):
        return SWING_DOWN
    return None


def classify(candles_4h: List[dict], candles_1h: Optional[List[dict]] = None) -> Dict[str, Any]:
    out = {"version": WEATHER_VERSION, "flag": UNKNOWN, "allow": ("LONG", "SHORT")}
    if len(candles_4h) < 20:
        return out
    atr_now = _atr(candles_4h)
    atr_old = _atr(candles_4h[:-10]) if len(candles_4h) >= 24 else None
    opening = bool(atr_now and atr_old and atr_old > 0 and atr_now / atr_old >= 1.4)
    up, down = _close_votes(candles_4h, 6)
    close_side = SWING_UP if up >= 5 else (SWING_DOWN if down >= 5 else None)
    body = _body_vote(candles_4h[-1])
    su = int(bool(opening and close_side != SWING_DOWN)) + int(close_side == SWING_UP) + int(body == SWING_UP)
    sd = int(bool(opening and close_side != SWING_UP)) + int(close_side == SWING_DOWN) + int(body == SWING_DOWN)
    if close_side == SWING_UP:
        su = max(su, 2)
    if close_side == SWING_DOWN:
        sd = max(sd, 2)
    if su >= 2 and su > sd:
        flag = SWING_UP
    elif sd >= 2 and sd > su:
        flag = SWING_DOWN
    else:
        flag = CHOP
    if flag in (SWING_UP, SWING_DOWN) and candles_1h and len(candles_1h) >= 8:
        u1, d1 = _close_votes(candles_1h, 6)
        if flag == SWING_UP and d1 >= 5:
            flag = CHOP
        elif flag == SWING_DOWN and u1 >= 5:
            flag = CHOP
    allow = ("LONG",) if flag == SWING_UP else (("SHORT",) if flag == SWING_DOWN else ("LONG", "SHORT"))
    out.update(flag=flag, allow=allow)
    return out


def side_allowed(flag: str, side: str) -> bool:
    s = (side or "").upper()
    if flag == SWING_UP:
        return s == "LONG"
    if flag == SWING_DOWN:
        return s == "SHORT"
    return s in ("LONG", "SHORT")
