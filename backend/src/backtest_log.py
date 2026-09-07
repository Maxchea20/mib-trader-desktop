"""Persistent log of backtest runs.

Each time a backtest is run via the API, its parameters + summary get
saved here (same sqlite file as everything else) so past runs can be
compared side-by-side instead of only ever seeing the most recent one.

Only the summary is stored, not the full equity curve / per-candle
points — those can be large and aren't needed for comparing runs at a
glance. Re-run with the same parameters if you want to see the detail
again.
"""
import sqlite3
import time
import uuid
from typing import Dict, List, Optional

from .market_data import database as mdb


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(mdb.db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS backtest_runs (
                id                      TEXT PRIMARY KEY,
                created_at              INTEGER NOT NULL,
                timeframe               TEXT NOT NULL,
                forward_candles         INTEGER,
                lookback                INTEGER,
                step                    INTEGER,
                evaluations             INTEGER,
                directional_trades      INTEGER,
                win_rate_pct            REAL,
                net_return_pct          REAL,
                avg_return_per_trade_pct REAL,
                long_count              INTEGER,
                short_count             INTEGER,
                wait_count              INTEGER,
                avoid_count             INTEGER,
                note                    TEXT
            );"""
        )
        conn.commit()


def record(timeframe: str, requested_lookback: int, summary: Dict, note: str = "") -> Dict:
    """Saves one run. `summary` is the dict returned by backtest.run_backtest()
    under the "summary" key — pass that in directly."""
    init_db()
    rid = str(uuid.uuid4())
    sc = summary.get("state_counts", {})
    row = {
        "id": rid,
        "created_at": int(time.time()),
        "timeframe": timeframe,
        "forward_candles": summary.get("forward_candles"),
        "lookback": requested_lookback,
        "step": summary.get("step"),
        "evaluations": summary.get("evaluations"),
        "directional_trades": summary.get("directional_trades"),
        "win_rate_pct": summary.get("win_rate_pct"),
        "net_return_pct": summary.get("net_return_pct"),
        "avg_return_per_trade_pct": summary.get("avg_return_per_trade_pct"),
        "long_count": sc.get("LONG", 0),
        "short_count": sc.get("SHORT", 0),
        "wait_count": sc.get("WAIT", 0),
        "avoid_count": sc.get("AVOID", 0),
        "note": note,
    }
    with _conn() as conn:
        conn.execute(
            """INSERT INTO backtest_runs
               (id, created_at, timeframe, forward_candles, lookback, step,
                evaluations, directional_trades, win_rate_pct, net_return_pct,
                avg_return_per_trade_pct, long_count, short_count, wait_count,
                avoid_count, note)
               VALUES (:id, :created_at, :timeframe, :forward_candles, :lookback,
                       :step, :evaluations, :directional_trades, :win_rate_pct,
                       :net_return_pct, :avg_return_per_trade_pct, :long_count,
                       :short_count, :wait_count, :avoid_count, :note)""",
            row,
        )
        conn.commit()
    return row


def list_runs(timeframe: Optional[str] = None, limit: int = 100) -> List[Dict]:
    init_db()
    with _conn() as conn:
        if timeframe:
            rows = conn.execute(
                "SELECT * FROM backtest_runs WHERE timeframe=? ORDER BY created_at DESC LIMIT ?",
                (timeframe, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def delete_run(run_id: str) -> bool:
    init_db()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM backtest_runs WHERE id=?", (run_id,))
        conn.commit()
        return cur.rowcount > 0


def clear(timeframe: Optional[str] = None) -> int:
    init_db()
    with _conn() as conn:
        if timeframe:
            cur = conn.execute("DELETE FROM backtest_runs WHERE timeframe=?", (timeframe,))
        else:
            cur = conn.execute("DELETE FROM backtest_runs")
        conn.commit()
        return cur.rowcount