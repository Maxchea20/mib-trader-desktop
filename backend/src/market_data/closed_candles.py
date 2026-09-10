"""Closed-candle eligibility — live and backtest must use the same rule.

A candle whose open time is `ts` on timeframe TF is CLOSED only when
    ts + TF_SECONDS[TF] <= now
The currently forming bar is never an input to analysis.
"""
from __future__ import annotations

import time
from typing import Iterable, List, Optional

from ..config import TF_SECONDS


def candle_is_closed(ts: int, timeframe: str, now: Optional[float] = None) -> bool:
    step = TF_SECONDS.get(timeframe)
    if not step:
        return True
    if now is None:
        now = time.time()
    return int(ts) + int(step) <= float(now)


def filter_closed(
    candles: Iterable[dict],
    timeframe: str,
    now: Optional[float] = None,
) -> List[dict]:
    """Return only candles that are fully closed as of `now`.

    Historical bars are always closed relative to wall-clock `now` when
    their period has ended. Backtest already passes prefixes of closed
    history; this filter is a no-op on those prefixes as long as the
    last bar's period has elapsed (true for any stored historical bar
    whose close is in the past).
    """
    if now is None:
        now = time.time()
    out = []
    for c in candles:
        ts = c.get("ts") if isinstance(c, dict) else None
        if ts is None:
            continue
        if candle_is_closed(int(ts), timeframe, now):
            out.append(c)
    return out
