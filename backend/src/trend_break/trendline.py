"""Causal pivot / ATR-slope trendlines.

Per timeframe, independently:
  * A pivot high at bar i is confirmed only when bar i + length has CLOSED.
    The same holds for pivot lows.
  * On confirmation the upper (descending) line is anchored at the pivot
    high and projected forward: value(t) = pivot_price - slope * (t - i).
    The lower (ascending) line is anchored at the pivot low:
    value(t) = pivot_price + slope * (t - i).
  * slope = ATR(period)[confirm bar] / length * mult, frozen for that line.
  * A break is the first CLOSED candle that crosses the projected line
    (close above the upper line / close below the lower line).  A line
    yields at most one break; a newer confirmed pivot replaces the line.

Nothing here reads a bar beyond the one being processed, so replaying a
prefix of history gives the same events as running live.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class Line:
    kind: str            # "upper" | "lower"
    pivot_index: int
    pivot_ts: int
    pivot_price: float
    confirm_index: int
    slope: float

    def value_at_index(self, t: float) -> float:
        d = self.slope * (t - self.pivot_index)
        return self.pivot_price - d if self.kind == "upper" else self.pivot_price + d

    def value_at_ts(self, ts: float, tf_seconds: int) -> float:
        return self.value_at_index(self.pivot_index + (ts - self.pivot_ts) / float(tf_seconds))


@dataclass(frozen=True)
class Break:
    direction: str       # LONG (close above upper) | SHORT (close below lower)
    index: int
    ts: int              # open time of the breaking candle
    close: float
    line_value: float    # projected line value at the breaking candle
    line: Line


@dataclass
class TrendlineResult:
    events: List[Break]
    upper: Optional[Line]
    lower: Optional[Line]
    atr: float
    last_index: int

    def latest_break(self) -> Optional[Break]:
        return self.events[-1] if self.events else None


def atr_series(candles: Sequence[dict], period: int = 14) -> List[Optional[float]]:
    """Wilder ATR. None until `period` true ranges are available."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n == 0:
        return out
    tr: List[float] = []
    for k, c in enumerate(candles):
        h, l = float(c["high"]), float(c["low"])
        if k == 0:
            tr.append(h - l)
        else:
            pc = float(candles[k - 1]["close"])
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if n < period:
        return out
    a = sum(tr[:period]) / period
    out[period - 1] = a
    for k in range(period, n):
        a = (a * (period - 1) + tr[k]) / period
        out[k] = a
    return out


def compute(candles: Sequence[dict], length: int = 14, mult: float = 1.0,
            atr_period: int = 14) -> TrendlineResult:
    n = len(candles)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    atrs = atr_series(candles, atr_period)
    upper: Optional[Line] = None
    lower: Optional[Line] = None
    up_done = lo_done = False
    events: List[Break] = []

    for t in range(n):
        i = t - length
        a = atrs[t]
        if i >= length and a is not None:
            slope = a / length * mult
            h = hi[i]
            if h > max(hi[i - length:i]) and h >= max(hi[i + 1:t + 1]):
                upper = Line("upper", i, int(candles[i]["ts"]), h, t, slope)
                up_done = False
            l = lo[i]
            if l < min(lo[i - length:i]) and l <= min(lo[i + 1:t + 1]):
                lower = Line("lower", i, int(candles[i]["ts"]), l, t, slope)
                lo_done = False
            fresh_up = upper is not None and upper.confirm_index == t
            fresh_lo = lower is not None and lower.confirm_index == t
        else:
            fresh_up = fresh_lo = False

        if upper is not None and not up_done and t >= 1:
            v, pv = upper.value_at_index(t), upper.value_at_index(t - 1)
            if cl[t] > v:
                up_done = True
                if cl[t - 1] <= pv:
                    events.append(Break(LONG, t, int(candles[t]["ts"]), cl[t], v, upper))
        if lower is not None and not lo_done and t >= 1:
            v, pv = lower.value_at_index(t), lower.value_at_index(t - 1)
            if cl[t] < v:
                lo_done = True
                if cl[t - 1] >= pv:
                    events.append(Break(SHORT, t, int(candles[t]["ts"]), cl[t], v, lower))

    last_atr = next((x for x in reversed(atrs) if x is not None), 0.0)
    return TrendlineResult(events, upper, lower, float(last_atr or 0.0), n - 1)


def direction(res: TrendlineResult, candles: Sequence[dict]) -> str:
    """LONG / SHORT / NEUTRAL from the trendline state.

    The most recent break defines the bias, and it holds while the last
    closed candle is still on the broken side of that (projected) line.
    """
    b = res.latest_break()
    if b is None or not candles:
        return "NEUTRAL"
    last = len(candles) - 1
    v = b.line.value_at_index(last)
    close = float(candles[-1]["close"])
    if b.direction == LONG and close > v:
        return LONG
    if b.direction == SHORT and close < v:
        return SHORT
    return "NEUTRAL"


def as_dict(res: TrendlineResult) -> Dict:
    def ln(x: Optional[Line]):
        if x is None:
            return None
        return {"pivot_ts": x.pivot_ts, "pivot_price": x.pivot_price,
                "slope": x.slope, "value_now": x.value_at_index(res.last_index)}
    return {"upper": ln(res.upper), "lower": ln(res.lower), "atr": res.atr}
