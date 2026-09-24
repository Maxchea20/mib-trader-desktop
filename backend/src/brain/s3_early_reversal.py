"""S3 -- Early Reversal / Exhaustion scenario. NEW, isolated module.

Does NOT modify scenario_engine.py's Thesis/Scenario logic. Detects a
directional M5 structural reversal (BOS/CHoCH) against the prevailing
context, but ONLY in the window where M15 produced no new structural
event this bar -- i.e. exactly where _m15_candidate() found nothing.

Per explicit design instruction: RSI is exhaustion CONTEXT, never the
trigger by itself. The actual trigger is always the M5 structural event
+ M1 confirmation. RSI here is checked as a RECENT RANGE (max/min over a
lookback), not a single current value -- price can overextend and
already be cooling by the time the structural break candle prints, so
checking only the instantaneous RSI at that moment would miss genuine
exhaustion setups. Reuses the 65/35 overbought/oversold convention
already established in momentum/observe.py rather than inventing new
thresholds.

Flow: detect() -> S3Candidate (structural event + RSI-range exhaustion
context) -> M1 confirmation -> ownership check (shared registry so a
later M15 confirmation of the SAME move doesn't duplicate an S1 entry)
-> same result shape as ScenarioEngine.tick()'s FIRE result, so it can
flow through the identical downstream confluence/risk/lifecycle/C-timing
pipeline without any special-casing there.

NOT called anywhere in the live 5-second loop yet. This module is
standalone and unit-testable on its own; wiring it into
scenario_live_bridge.py behind CONFIG["s3_enabled"] is a deliberately
separate, later step -- per the design brief's own ordering, backtesting
comes before any live wiring, not after.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..contract import LONG, SHORT
from ..indicators import arrays, atr as _atr
from ..structure.observe import observe as obs_structure

# Reuses the existing 65/35 overbought/oversold convention already
# established in momentum/observe.py (line ~129) -- not a new threshold.
RSI_OVERBOUGHT = 65.0
RSI_OVERSOLD = 35.0
# Starting point only, per the sign-off ("we adjust it along the way") --
# not yet validated against historical data.
RSI_LOOKBACK_BARS = 14

# A later M15-confirmed thesis on the same move, within this window,
# is treated as the SAME reversal S3 already consumed -- not a new one.
OWNERSHIP_WINDOW_SECONDS = 15 * 60
OWNERSHIP_TOLERANCE_ATR = 0.3


@dataclass
class S3Candidate:
    direction: str
    origin_level: float
    origin_ts: int
    origin_event: str
    rsi_recent_max: float
    rsi_recent_min: float
    exhaustion_side: str          # "OVERBOUGHT" or "OVERSOLD"
    atr15: float
    m1_confirmed: bool = False
    m1_confirm_ts: Optional[int] = None
    m1_confirm_event: Optional[str] = None


class ReversalOwnership:
    """Shared dedup registry. scenario_engine.py's own thesis-creation
    path is expected to check is_owned() too (a separate, later wiring
    step) so S1 doesn't open a duplicate thesis for a move S3 already
    fired on. Kept as an instance (not module-level globals) so tests
    can run in full isolation from each other and from any live
    process's real registry."""

    def __init__(self):
        self._owned: List[Dict] = []  # [{direction, level, ts}]

    def is_owned(self, direction: str, level: float, ts: int, atr15: float) -> bool:
        self._expire(ts)
        tol = OWNERSHIP_TOLERANCE_ATR * atr15 if atr15 else 0.0
        for o in self._owned:
            if o["direction"] == direction and abs(o["level"] - level) <= tol:
                return True
        return False

    def own(self, direction: str, level: float, ts: int) -> None:
        self._owned.append({"direction": direction, "level": level, "ts": ts})

    def _expire(self, now_ts: int) -> None:
        self._owned = [o for o in self._owned if now_ts - o["ts"] <= OWNERSHIP_WINDOW_SECONDS]


def _rsi_range(closed_5m: List[dict], lookback: int = RSI_LOOKBACK_BARS, period: int = 14) -> Dict[str, float]:
    """RSI computed at each of the last `lookback` closed bars (not just
    the current one), so an overextension that already started cooling
    by the current bar is still visible in rsi_recent_max/min.

    Uses a fixed-size local window and a single delta pass -- NOT
    arrays()/rsi() called per position on an ever-growing full-history
    slice, which is O(total history) per call and made a real backtest
    run far too slowly (measured directly: it timed out at scale before
    this fix). Numerically equivalent to calling the existing rsi()
    independently at each position; this only changes how it's computed,
    not what it returns.
    """
    import numpy as np
    needed = lookback + period + 1
    if len(closed_5m) < needed:
        return {"max": 50.0, "min": 50.0}
    local = closed_5m[-needed:]
    closes = np.array([c["close"] for c in local], dtype=float)
    delta = np.diff(closes)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    vals = []
    for end in range(period, len(delta) + 1):
        avg_gain = gains[end - period:end].mean()
        avg_loss = losses[end - period:end].mean()
        vals.append(100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
    return {"max": max(vals), "min": min(vals)} if vals else {"max": 50.0, "min": 50.0}


def detect_candidate(candles_15m: List[dict], closed_5m: List[dict],
                      m15_had_fresh_event: bool, ownership: ReversalOwnership) -> Optional[S3Candidate]:
    """M5 structural event + RSI-range exhaustion context + ownership
    check ONLY -- no M1 involved. Returns an unconfirmed S3Candidate
    (m1_confirmed=False) or None. Split out from the old all-in-one
    detect() so a caller (live poll or backtest) can check M1
    confirmation across GENUINELY LATER candles as they arrive, rather
    than only the single instant this candidate was found -- checking
    M1 at the same instant as the M5 close is not independent
    confirmation, since a 5m candle's close equals its last constituent
    1m candle's close by construction."""
    if m15_had_fresh_event or not closed_5m or len(closed_5m) < 60:
        return None

    st5 = obs_structure(closed_5m, "5m")
    bar_ts = int(closed_5m[-1]["ts"])
    fresh5 = [e for e in (st5.history or []) if e.timestamp == bar_ts and e.event_type in ("BOS", "CHoCH")]
    if not fresh5:
        return None
    event = fresh5[-1]
    direction = LONG if event.direction == "BULLISH" else (SHORT if event.direction == "BEARISH" else None)
    if direction is None:
        return None

    rng = _rsi_range(closed_5m)
    if direction == SHORT and rng["max"] >= RSI_OVERBOUGHT:
        exhaustion_side = "OVERBOUGHT"
    elif direction == LONG and rng["min"] <= RSI_OVERSOLD:
        exhaustion_side = "OVERSOLD"
    else:
        return None  # structural event alone, without exhaustion context, is not an S3 candidate

    aa15 = arrays(candles_15m) if candles_15m else None
    atr15 = float(_atr(aa15["high"], aa15["low"], aa15["close"], 14) or 0.0) if aa15 is not None else 0.0

    level = float(event.reference_price or event.price)
    if ownership.is_owned(direction, level, bar_ts, atr15):
        return None  # already consumed by S1/S2 or a prior S3 fire on this same move

    return S3Candidate(
        direction=direction, origin_level=level, origin_ts=bar_ts, origin_event=event.event_type,
        rsi_recent_max=rng["max"], rsi_recent_min=rng["min"], exhaustion_side=exhaustion_side,
        atr15=atr15,
    )


def check_m1_confirmation(cand: S3Candidate, closed_1m: List[dict]) -> bool:
    """Checks whether the LATEST 1m candle in `closed_1m` confirms
    `cand`'s direction with a fresh BOS/CHoCH. Caller is responsible for
    only passing 1m data from AFTER cand.origin_ts, and for calling this
    repeatedly as genuinely new 1m closes arrive (same polling pattern
    as EntryTimingCWatcher) -- this function itself does not enforce
    that ordering, it only evaluates whatever's the latest candle in
    what it's given. Mutates cand in place and returns True on the tick
    it actually confirms."""
    if not closed_1m or len(closed_1m) < 60:
        return False
    st1 = obs_structure(closed_1m, "1m")
    bar_ts1 = int(closed_1m[-1]["ts"])
    fresh1 = [e for e in (st1.history or []) if e.timestamp == bar_ts1 and e.event_type in ("BOS", "CHoCH")]
    for e in fresh1:
        d1 = LONG if e.direction == "BULLISH" else (SHORT if e.direction == "BEARISH" else None)
        if d1 == cand.direction:
            cand.m1_confirmed = True
            cand.m1_confirm_ts = bar_ts1
            cand.m1_confirm_event = e.event_type
            return True
    return False


def detect(candles_15m: List[dict], closed_5m: List[dict], closed_1m: List[dict],
           m15_had_fresh_event: bool, ownership: ReversalOwnership) -> Optional[S3Candidate]:
    """Live single-tick convenience wrapper: finds a candidate and
    checks confirmation against whatever `closed_1m` is passed in one
    shot. Correct for the live poll (called every 5s with the real,
    continuously-advancing 1m feed -- confirmation genuinely happens on
    a LATER tick's call, not this same one, because ownership.own() is
    only recorded on confirmation and a real M1 close hasn't happened
    yet at the moment the M5 candidate first appears). NOT valid to call
    once with a static historical snapshot and expect a meaningful
    confirmation check on the same call -- see check_m1_confirmation()
    for that case (backtests, and the live watcher's own poll loop)."""
    cand = detect_candidate(candles_15m, closed_5m, m15_had_fresh_event, ownership)
    if cand is None:
        return None
    if not check_m1_confirmation(cand, closed_1m):
        return None
    ownership.own(cand.direction, cand.origin_level, cand.origin_ts)
    return cand


def candidate_to_result(cand: S3Candidate, price: float, thesis_id: str) -> Dict:
    """Same result shape as ScenarioEngine.tick()'s FIRE result, so
    downstream code (confluence/risk/lifecycle/C-timing) needs no
    special-casing for S3 vs S1/S2."""
    return {
        "ts": cand.m1_confirm_ts, "direction": cand.direction, "thesis_id": thesis_id,
        "thesis": f"M5 {cand.origin_event} ({cand.direction.lower()}) with {cand.exhaustion_side.lower()} "
                  f"exhaustion context, M1-confirmed before any M15 structural event.",
        "scenario": "S3_EARLY_REVERSAL", "action": "FIRE", "entry": price, "fire_id": f"{thesis_id}/S3-FIRE",
        "what_happening": f"Early reversal: M5 {cand.origin_event}, M1 confirmed.",
        "why": f"RSI recent range {cand.rsi_recent_min:.1f}-{cand.rsi_recent_max:.1f} shows "
               f"{cand.exhaustion_side.lower()} exhaustion; M15 has not yet confirmed this move.",
        "debug": {
            "thesis": {
                "thesis_id": thesis_id, "direction": cand.direction, "origin_event": cand.origin_event,
                "origin_level": cand.origin_level, "origin_ts": cand.origin_ts,
            },
            "atr15": cand.atr15, "rsi_recent_max": cand.rsi_recent_max, "rsi_recent_min": cand.rsi_recent_min,
            "exhaustion_side": cand.exhaustion_side, "m1_confirm_ts": cand.m1_confirm_ts,
            "m1_confirm_event": cand.m1_confirm_event,
        },
    }