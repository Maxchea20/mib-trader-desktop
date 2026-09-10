"""Read-only data access layer for the analysis agents.

Agents MUST read market history through this layer only. It never mutates data
and knows nothing about analysis logic.
"""
import time
from typing import List, Dict, Optional

from . import database as db
from ..config import SYMBOL, TIMEFRAMES, TF_SECONDS


def read_candles(timeframe: str, limit: int = 1000, symbol: str = SYMBOL) -> List[Dict]:
    return db.get_candles(symbol, timeframe, limit=limit)


def read_closed_candles(timeframe: str, limit: int = 1000, symbol: str = SYMBOL,
                         now: Optional[float] = None) -> List[Dict]:
    """Same as read_candles(), but guarantees every returned candle has
    ALREADY CLOSED -- it never returns a still-forming "current" bar as
    the last element.

    Why this exists: market_data.manager._poll_loop() re-syncs the
    latest candle from MEXC every ~60s (gap_sync.sync_latest(), whose
    own docstring says "small window for live forming candle") -- so
    the newest row in the candles table can, at any moment, be a bar
    that hasn't closed yet, with a high/low/close/volume that's still
    changing. A historical backtest replay never has this problem
    (every bar in history is, by definition, already closed), so
    without this function, LIVE analysis and BACKTEST replay could see
    materially different "current candle" states for what's nominally
    the same signal -- confirmed and documented in
    MIB_BREAKOUT_MATH_AUDIT.txt, Section 6.

    Anything that feeds an agent's analyze() (analysis_service.py,
    autotrader.py, backtest.py, backtest_walkforward.py) should call
    THIS function, not read_candles(). Chart/ticker consumers
    (server.py's /market/candles endpoint, the live price feed in
    market_data.manager) should keep calling read_candles() directly --
    they WANT the forming candle so the chart updates in real time.
    That's a legitimate, separate concern from what an agent analyzes.

    Fetches one extra candle so that, after dropping a still-forming
    tail bar, the caller still gets `limit` closed candles back
    (matches what it would have gotten from read_candles() once that
    same bar eventually closes).
    """
    step = TF_SECONDS.get(timeframe)
    raw = db.get_candles(symbol, timeframe, limit=limit + 1)
    if not raw or not step:
        return raw
    now = time.time() if now is None else now
    if raw[-1]["ts"] + step > now:
        raw = raw[:-1]  # last candle hasn't closed yet -- drop it
    return raw[-limit:]


def read_sync_status(symbol: str = SYMBOL) -> Dict:
    out = {}
    for tf in TIMEFRAMES:
        cnt = db.count_candles(symbol, tf)
        mn, mx = db.bounds(symbol, tf)
        meta = db.get_sync_meta(symbol, tf)
        out[tf] = {
            "count": cnt,
            "first_ts": mn,
            "last_ts": mx,
            "last_sync": meta.get("last_sync"),
            "status": meta.get("last_status"),
        }
    return {"symbol": symbol, "db_path": db.db_path(), "timeframes": out}


def has_data(timeframe: str, symbol: str = SYMBOL) -> bool:
    return db.count_candles(symbol, timeframe) > 0