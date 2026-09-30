"""Causal pivot / ATR-slope trendlines.

Per timeframe, independently:
  * A pivot high at bar i is confirmed only when bar i + length has CLOSED.
    The same holds for pivot lows.
  * On confirmation the upper (descending) line is anchored at the pivot
    high and projected forward: value(t) = pivot_price - slope * (t - i).
    The lower (ascending) line is anchored at the pivot low:
    value(t) = pivot_price + slope * (t - i).
  * slope = ATR(period)[confirm bar] / length * mult, frozen for that line.
  * A break is the first CLOSED candle after the pivot confirmation bar whose
    close is above the projected upper line / below the projected lower line
    (0 -> 1 edge of the up/down state).  The state resets to 0 on each newly
    confirmed pivot, and that confirmation bar is not itself tested.

Nothing here reads a bar beyond the one being processed, so replaying a
prefix of history gives the same events as running live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    lines: List[Line] = field(default_factory=list)   # every pivot line, oldest first (display only)

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
    upos = dnos = 0          # 1 once a close has crossed the line since its pivot
    events: List[Break] = []
    history: List[Line] = []

    for t in range(n):
        i = t - length
        a = atrs[t]
        ph = pl = False
        if i >= length and a is not None:
            slope = a / length * mult
            h = hi[i]
            if h > max(hi[i - length:i]) and h >= max(hi[i + 1:t + 1]):
                upper = Line("upper", i, int(candles[i]["ts"]), h, t, slope)
                history.append(upper)
                ph = True
            l = lo[i]
            if l < min(lo[i - length:i]) and l <= min(lo[i + 1:t + 1]):
                lower = Line("lower", i, int(candles[i]["ts"]), l, t, slope)
                history.append(lower)
                pl = True

        # upos := ph ? 0 : close > upper - slope_ph * length ? 1 : upos
        if ph:
            upos = 0
        elif upper is not None:
            v = upper.value_at_index(t)
            if cl[t] > v:
                if upos == 0:
                    events.append(Break(LONG, t, int(candles[t]["ts"]), cl[t], v, upper))
                upos = 1
        # dnos := pl ? 0 : close < lower + slope_pl * length ? 1 : dnos
        if pl:
            dnos = 0
        elif lower is not None:
            v = lower.value_at_index(t)
            if cl[t] < v:
                if dnos == 0:
                    events.append(Break(SHORT, t, int(candles[t]["ts"]), cl[t], v, lower))
                dnos = 1

    last_atr = next((x for x in reversed(atrs) if x is not None), 0.0)
    return TrendlineResult(events, upper, lower, float(last_atr or 0.0), n - 1, history)


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


def structure_direction(candles: Sequence[dict], length: int = 14) -> Dict:
    """Persistent swing-structure trend (LONG / SHORT / NEUTRAL).

    Uses the same causally confirmed pivots as the trendlines (a pivot only
    counts once `length` bars to its right have closed).  Each time a pivot
    is confirmed, and at least two pivot highs and two pivot lows exist:
      * latest high > previous high AND latest low > previous low -> LONG
      * latest high < previous high AND latest low < previous low -> SHORT
      * otherwise the previous direction is kept.
    It starts NEUTRAL and stays NEUTRAL until a trend is confirmed, so it
    only flips when the structure flips (slowly on high timeframes).
    """
    n = len(candles)
    hi = [float(c["high"]) for c in candles]
    lo = [float(c["low"]) for c in candles]
    highs: List[tuple] = []      # (pivot_index, price)
    lows: List[tuple] = []
    state = "NEUTRAL"
    since_ts: Optional[int] = None
    for t in range(2 * length, n):
        i = t - length
        changed = False
        if hi[i] > max(hi[i - length:i]) and hi[i] >= max(hi[i + 1:t + 1]):
            highs.append((i, hi[i]))
            changed = True
        if lo[i] < min(lo[i - length:i]) and lo[i] <= min(lo[i + 1:t + 1]):
            lows.append((i, lo[i]))
            changed = True
        if changed and len(highs) >= 2 and len(lows) >= 2:
            up = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
            dn = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]
            new = LONG if up else SHORT if dn else state
            if new != state:
                state, since_ts = new, int(candles[t]["ts"])
    return {
        "direction": state, "since_ts": since_ts,
        "last_highs": [p for _, p in highs[-2:]], "last_lows": [p for _, p in lows[-2:]],
    }
