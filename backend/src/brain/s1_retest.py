"""S1 retest-and-rejection entry (replaces the piercing C trigger for S1 only; S2 keeps tick_c).

After S1 arms on a fresh M5 BOS/CHoCH, 1m candles that closed AFTER the arming moment are
checked in order:
  1. retest   LONG: low <= level + RETEST_ATR*ATR     SHORT: high >= level - RETEST_ATR*ATR
  2. hold     LONG: close >= level                    SHORT: close <= level
  3. turn     LONG: close > open                      SHORT: close < open
The first candle at/after the first retest that satisfies 2 and 3 triggers the entry at its close.
Everything must complete by the deadline (S1 TTL: 15m breakout close + 30 min); a candle closing
after it is never used, and reaching the deadline without an entry cancels the setup.
Before any retest, a 1m high (LONG) / low (SHORT) beyond level +/- MAX_EXTENSION_ATR*ATR cancels
the setup (no chasing an over-extended impulse).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..contract import LONG

RETEST_ATR = 0.25
MAX_EXTENSION_ATR = 1.5


def start_retest_watch(state: dict, arm_ts: int, level: float, direction: str, atr: float,
                       deadline: Optional[int] = None) -> None:
    state["phase"] = "C_WATCH"
    state["path"] = "S1"
    state["c_watch"] = {
        "path": "S1", "mode": "retest", "window_open_ts": int(arm_ts),
        "structural_level": float(level), "direction": direction, "atr": float(atr),
        "deadline": int(deadline) if deadline is not None else None,
    }


def tick_retest(state: dict, one_minute_candles: List[dict]) -> str:
    """SUCCESS (result stored in c_watch['result']), CANCEL, or NONE (keep watching)."""
    w = state.get("c_watch") or {}
    got = retest_entry(w.get("direction"), w.get("structural_level"), w.get("atr"),
                       w.get("window_open_ts"), one_minute_candles, w.get("deadline"))
    if got is None:
        return "NONE"
    if got.get("cancel"):
        w["cancel_reason"] = got["cancel"]
        return "CANCEL"
    w["result"] = got
    return "SUCCESS"


def retest_entry(direction: Optional[str], level, atr, open_ts, one_minute_candles: List[dict],
                 deadline: Optional[int] = None) -> Optional[Dict]:
    if direction is None or level is None or open_ts is None or not atr or atr <= 0:
        return None
    level, atr, long_ = float(level), float(atr), direction == LONG
    zone = level + RETEST_ATR * atr if long_ else level - RETEST_ATR * atr
    too_far = level + MAX_EXTENSION_ATR * atr if long_ else level - MAX_EXTENSION_ATR * atr
    touched = False
    rows = sorted((c for c in one_minute_candles or [] if int(c["ts"]) >= int(open_ts)), key=lambda c: c["ts"])
    for c in rows:
        if deadline is not None and int(c["ts"]) + 60 > int(deadline):
            return {"cancel": "TTL: no retest entry within 30 min of the 15m breakout close"}
        hi, lo, op, cl = float(c["high"]), float(c["low"]), float(c["open"]), float(c["close"])
        if not touched and ((lo <= zone) if long_ else (hi >= zone)):
            touched = True
        if not touched:
            if (hi >= too_far) if long_ else (lo <= too_far):
                return {"cancel": "extended beyond %.1f ATR before any retest" % MAX_EXTENSION_ATR}
            continue
        holds = cl >= level if long_ else cl <= level
        turns = cl > op if long_ else cl < op
        if holds and turns:
            return {"entry_ts": int(c["ts"]) + 60, "entry_price": cl}
    if deadline is not None and rows and int(rows[-1]["ts"]) + 60 >= int(deadline):
        return {"cancel": "TTL: no retest entry within 30 min of the 15m breakout close"}
    return None
