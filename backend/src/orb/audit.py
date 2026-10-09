"""Read-only audit of the historical candle database.

The primary file is backend/market_data_clean.db. In git it is a Git LFS
pointer (~133 bytes). A real SQLite file is larger than 10 KB; anything
smaller is refused so a pointer is never parsed as prices.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .market import Bar
from .session import NY, is_nyse_session_day, ny_date_of_utc_ts, range_bounds_ts


def _is_lfs_pointer(path: Path) -> bool:
    try:
        head = path.read_bytes()[:80]
    except OSError:
        return False
    return head.startswith(b"version https://git-lfs.github.com/spec/v1")


def resolve_market_db(explicit: str | None = None) -> Path | None:
    """Return a real SQLite file. An LFS pointer is not a database.

    `market_data.db` is not a silent substitute. The ORB study uses
    `market_data_clean.db` only.
    """
    if explicit:
        path = Path(explicit)
        if path.is_file() and path.stat().st_size > 10_000 and not _is_lfs_pointer(path):
            return path.resolve()
        return None
    here = Path(__file__).resolve().parents[2]
    env = os.environ.get("MARKET_DB_PATH", "").strip()
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(here / "market_data_clean.db")
    for path in candidates:
        if path.is_file() and path.stat().st_size > 10_000 and not _is_lfs_pointer(path):
            return path.resolve()
    return None


def database_status(explicit: str | None = None) -> dict:
    here = Path(__file__).resolve().parents[2]
    clean = here / "market_data_clean.db"
    other = here / "market_data.db"
    return {
        "resolved": None if resolve_market_db(explicit) is None else str(resolve_market_db(explicit)),
        "clean_db_present": clean.is_file(),
        "clean_db_bytes": clean.stat().st_size if clean.is_file() else 0,
        "clean_db_is_lfs_pointer": clean.is_file() and _is_lfs_pointer(clean),
        "market_data_db_bytes": other.stat().st_size if other.is_file() else 0,
        "market_data_db_note": (
            "backend/market_data.db is a separate live/decision database with a short "
            "candle tail. It is not the ORB source and is not substituted for the clean file."
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_bars(path: Path, symbol: str, timeframe: str) -> list[Bar]:
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND timeframe=? ORDER BY ts",
            (symbol, timeframe),
        ).fetchall()
    finally:
        con.close()
    bars = [
        Bar(int(ts), float(o), float(h), float(l), float(c), float(v or 0.0))
        for ts, o, h, l, c, v in rows
    ]
    return bars


def audit_database(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        coverage = []
        for symbol, timeframe, n, mn, mx in con.execute(
            "SELECT symbol, timeframe, COUNT(*), MIN(ts), MAX(ts) "
            "FROM candles GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
        ):
            scale = 1000 if mn and mn > 10_000_000_000 else 1
            coverage.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "rows": int(n),
                    "start_utc": datetime.fromtimestamp(mn / scale, timezone.utc).isoformat(),
                    "end_utc": datetime.fromtimestamp(mx / scale, timezone.utc).isoformat(),
                }
            )
        dupes = con.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM candles GROUP BY symbol, timeframe, ts HAVING COUNT(*)>1)"
        ).fetchone()[0]
        bad = con.execute(
            "SELECT COUNT(*) FROM candles WHERE high<low OR high<open OR high<close "
            "OR low>open OR low>close OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL"
        ).fetchone()[0]
        has_bid_ask = any(name in tables for name in ("quotes", "ticks", "trades", "order_book", "depth"))
        spreads = []
        if "decisions" in tables:
            cols = [r[1] for r in con.execute("PRAGMA table_info(decisions)")]
            if "spread" in cols:
                spreads = [float(r[0]) for r in con.execute(
                    "SELECT spread FROM decisions WHERE spread IS NOT NULL"
                ) if r[0] is not None]
    finally:
        con.close()
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "tables": tables,
        "symbols_timeframes": coverage,
        "duplicate_candle_groups": int(dupes),
        "invalid_ohlc_rows": int(bad),
        "bid_ask_trades_or_book_tables": has_bid_ask,
        "decision_spread_samples": {
            "n": len(spreads),
            "values": spreads,
            "note": (
                "These are live decision-log snapshots stored in the same file, "
                "not a historical bid/ask series. They are not used as a spread path."
            ),
        },
    }


def session_coverage(bars: list[Bar], bar_seconds: int) -> dict[str, Any]:
    """How many New York opening windows are fully present."""
    by_ts = {b.ts: b for b in bars}
    dates = sorted({ny_date_of_utc_ts(b.ts) for b in bars})
    gaps = []
    ordered = sorted(by_ts)
    missing = 0
    for left, right in zip(ordered, ordered[1:]):
        delta = right - left
        if delta != bar_seconds:
            miss = max(0, delta // bar_seconds - 1)
            missing += miss
            if len(gaps) < 8:
                gaps.append(
                    {
                        "from_utc": datetime.fromtimestamp(left, timezone.utc).isoformat(),
                        "to_utc": datetime.fromtimestamp(right, timezone.utc).isoformat(),
                        "missing_bars": miss,
                    }
                )
    windows = {}
    for minutes, name in ((5, "ORB-5"), (15, "ORB-15"), (30, "ORB-30")):
        complete = nyse = incomplete = skipped = 0
        for day in dates:
            if not is_nyse_session_day(day):
                skipped += 1
                continue
            nyse += 1
            start, end = range_bounds_ts(day, minutes)
            if bar_seconds <= 0 or (end - start) % bar_seconds != 0:
                incomplete += 1
                continue
            expected = (end - start) // bar_seconds
            if expected <= 0 or any((start + i * bar_seconds) not in by_ts for i in range(expected)):
                incomplete += 1
            else:
                complete += 1
        windows[name] = {
            "complete_nyse_sessions": complete,
            "nyse_dates_in_span": nyse,
            "incomplete_nyse_sessions": incomplete,
            "non_session_dates_with_bars": skipped,
        }
    return {
        "bars": len(bars),
        "gap_events_listed": gaps,
        "missing_bars_estimate": missing,
        "ny_dates_with_any_bar": len(dates),
        "first_ny_date": dates[0].isoformat() if dates else None,
        "last_ny_date": dates[-1].isoformat() if dates else None,
        "windows": windows,
        "timezone": str(NY),
    }
