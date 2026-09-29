"""Break-candle quality (15M / 5M) and CHoCH/BOS confidence gauge.

Both are graded scores in 0..1, never setups.  They only inform confidence,
apart from a very low configurable floor that rejects clearly dead breaks.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from .trendline import LONG, SHORT, atr_series


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def candle_score(c: dict, direction: str, atr: float) -> float:
    o, cl = float(c["open"]), float(c["close"])
    rng = float(c["high"]) - float(c["low"])
    signed = (cl - o) if direction == LONG else (o - cl)
    if signed <= 0 or rng <= 0 or atr <= 0:
        return 0.0
    body_atr = _clamp((signed / atr) / 0.8)       # 0.8 ATR body = full marks
    body_ratio = _clamp((signed / rng) / 0.6)     # body >= 60% of range = full marks
    return 0.6 * body_atr + 0.4 * body_ratio


def break_quality(
    candles: Sequence[dict],
    direction: str,
    line_value_at: Callable[[int], float],
    from_ts: int,
    tf_seconds: int,
    window: int = 6,
    atr_period: int = 14,
) -> Dict:
    """Score how healthily price is expanding away from the broken line.

    `candles` must be closed candles only.  Only candles opened at or after
    `from_ts` are inspected (at most the last `window`).
    """
    atrs = atr_series(candles, atr_period)
    idx = [k for k, c in enumerate(candles) if int(c["ts"]) >= from_ts][-window:]
    if not idx:
        return {"score": 0.0, "label": "NONE", "n": 0, "peak": 0.0, "recent": 0.0, "side_frac": 0.0}
    scores: List[float] = []
    on_side = 0
    for k in idx:
        c = candles[k]
        a = atrs[k] or atrs[max(0, k - 1)] or 0.0
        if not a:
            a = float(c["high"]) - float(c["low"])
        scores.append(candle_score(c, direction, a))
        lv = line_value_at(int(c["ts"]))
        cl = float(c["close"])
        if (direction == LONG and cl > lv) or (direction == SHORT and cl < lv):
            on_side += 1
    peak = max(scores)
    recent = sum(scores[-3:]) / len(scores[-3:])
    side_frac = on_side / len(idx)
    score = 0.5 * peak + 0.3 * recent + 0.2 * side_frac
    label = "GOOD" if score >= 0.6 else "OK" if score >= 0.35 else "POOR"
    return {"score": round(score, 4), "label": label, "n": len(idx),
            "peak": round(peak, 4), "recent": round(recent, 4),
            "side_frac": round(side_frac, 4)}


def _pivots(candles: Sequence[dict], lr: int):
    """Confirmed lr-bar pivots as (index, confirm_index, price)."""
    highs, lows = [], []
    n = len(candles)
    for i in range(lr, n - lr):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        if all(h > float(candles[i - k]["high"]) and h >= float(candles[i + k]["high"]) for k in range(1, lr + 1)):
            highs.append((i, i + lr, h))
        if all(l < float(candles[i - k]["low"]) and l <= float(candles[i + k]["low"]) for k in range(1, lr + 1)):
            lows.append((i, i + lr, l))
    return highs, lows


def structure_events(candles: Sequence[dict], lr: int = 2) -> List[Dict]:
    """Causal CHoCH/BOS events from closes vs confirmed swing levels."""
    highs, lows = _pivots(candles, lr)
    events: List[Dict] = []
    trend: Optional[str] = None
    used_h = used_l = -1
    for t, c in enumerate(candles):
        cl = float(c["close"])
        ph = [p for p in highs if p[1] <= t and p[0] > used_h]
        pl = [p for p in lows if p[1] <= t and p[0] > used_l]
        if ph and cl > ph[-1][2]:
            events.append({"type": "BOS" if trend == LONG else "CHoCH", "direction": LONG,
                           "ts": int(c["ts"]), "level": ph[-1][2]})
            used_h = ph[-1][0]
            trend = LONG
        elif pl and cl < pl[-1][2]:
            events.append({"type": "BOS" if trend == SHORT else "CHoCH", "direction": SHORT,
                           "ts": int(c["ts"]), "level": pl[-1][2]})
            used_l = pl[-1][0]
            trend = SHORT
    return events


def structure_gauge(candles_15m: Sequence[dict], candles_5m: Sequence[dict],
                    direction: str, since_ts: int) -> Dict:
    """Confidence only.  Aligned BOS/CHoCH raise it, opposing events lower it."""
    aligned: List[Dict] = []
    against: List[Dict] = []
    for tf, rows in (("15m", candles_15m), ("5m", candles_5m)):
        for e in structure_events(rows):
            if e["ts"] < since_ts:
                continue
            (aligned if e["direction"] == direction else against).append({**e, "tf": tf})
    last_aligned = aligned[-1] if aligned else None
    last_against = against[-1] if against else None
    if last_aligned and (not last_against or last_aligned["ts"] >= last_against["ts"]):
        state = "BOS" if last_aligned["type"] == "BOS" else "CHOCH"
        score = 1.0 if state == "BOS" else 0.8
    elif last_against and not last_aligned:
        state, score = "AGAINST", 0.2
    elif last_against:
        state, score = "MIXED", 0.35
    else:
        state, score = "NONE", 0.5
    return {"state": state, "score": score, "aligned": aligned[-3:], "against": against[-3:]}
