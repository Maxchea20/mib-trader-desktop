"""Entry-Timing Layer C — ISOLATED. NOT imported by scenario_engine.py,
NOT wired into any live execution path, NOT called by autotrader_loop.py
or anything else. This is a standalone refinement layer for backtest/
research use only.

Scope, precisely: this module does NOT create theses, does NOT detect
M15 structure, does NOT detect the M5 fast-pivot cross, and does NOT
decide SL/TP. All of that is the existing, UNCHANGED job of
scenario_engine.py's _m15_candidate() / _fast_pivots_5m() /
_m5_confirmation() / the Thesis/Scenario state machine. This layer
starts only AFTER that existing confluence logic has already identified
a qualifying M5#2 breakout/cross for a given thesis, and asks one
narrow question:

    "Given that this setup is already valid, what is the EARLIEST
     1-minute-resolution moment we could have acted on it, using only
     information available at that exact moment?"

Rule (unchanged from the research phase, not re-tuned here):
  - Walk the 1-minute candles that make up the M5#2 five-minute window,
    in order.
  - Fire at the CLOSE of the first one whose own close crosses the same
    structural level the M5 layer is already confirming against.
  - No hardcoded minute number. Minute 1 through minute 5 are all
    treated identically -- whichever one qualifies first, fires first.
  - If 1-minute data does not cover this window (or is incomplete),
    return None. Callers must fall back to the existing entry logic
    themselves; this layer never approximates or guesses.
"""
from typing import Dict, List, Optional


def find_m5_2_intrabar_entry(direction: str, structural_level: float,
                              m5_2_open_ts: int,
                              one_minute_candles: List[dict]) -> Optional[Dict]:
    """
    direction: "LONG" or "SHORT" -- from the existing, unchanged thesis.
    structural_level: the existing, unchanged M15 origin_level (or
        whatever level the existing M5 fast-pivot confirmation is itself
        confirming against -- this layer does not redefine it).
    m5_2_open_ts: open timestamp of the M5#2 five-minute candle, as
        already identified by the existing confluence logic. This
        function does not decide which 5-minute candle is "M5#2".
    one_minute_candles: the 1-minute candles covering that M5#2 window,
        each a dict with at least ts/open/high/low/close, in ascending
        ts order. Pass exactly what is available -- if fewer than 5 are
        present because the data doesn't cover the full window, this
        function still checks what it was given and returns None only
        if none of them qualify (it does not require exactly 5).

    Returns None if:
      - one_minute_candles is empty, or
      - no supplied candle's close ever crosses structural_level in the
        thesis direction (including the case where the caller simply
        has no 1-minute data for this window at all -- that decision is
        the caller's to make, not this function's to paper over).

    Otherwise returns a dict describing the earliest qualifying moment:
      entry_ts: the confirming 1-minute candle's own close time
                (candle_ts + 60s -- candles are timestamped by open)
      entry_price: that candle's close price
      confirmed_at_minute: 1-indexed position within the supplied
                candles (informational only -- NOT used as a rule;
                minute 1 and minute 5 are evaluated identically)
      seconds_after_m5_2_open: informational timing offset
    """
    if not one_minute_candles:
        return None
    for i, c in enumerate(one_minute_candles, start=1):
        crossed = (c["close"] > structural_level) if direction == "LONG" else (c["close"] < structural_level)
        if crossed:
            return {
                "entry_ts": c["ts"] + 60,
                "entry_price": c["close"],
                "confirmed_at_minute": i,
                "seconds_after_m5_2_open": c["ts"] - m5_2_open_ts,
            }
    return None