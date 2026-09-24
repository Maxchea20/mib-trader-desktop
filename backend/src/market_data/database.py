"""Persistent local SQLite market-data database."""
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import List, Dict, Optional

_ROOT = Path(__file__).resolve().parents[2]
_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_DB_PATH = None


def _data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "mib-trader"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mib-trader"
    return Path.home() / ".local" / "share" / "mib-trader"


def resolve_db_path() -> str:
    """Prefer a real market_data_clean.db over an empty market_data.db."""
    names = ("market_data_clean.db", "market_data.db")
    roots = [
        _ROOT,
        _ROOT.parent,
        _data_dir(),
        Path.home() / "Downloads" / "mib-trader-desktop-full" / "mib-trader-desktop" / "backend",
    ]
    found = []
    env = os.environ.get("MARKET_DB_PATH", "").strip()
    if env:
        p = Path(env)
        if p.is_file() and p.stat().st_size > 10_000:
            found.append(p)
    for root in roots:
        try:
            for name in names:
                p = root / name
                if p.is_file() and p.stat().st_size > 10_000:
                    found.append(p.resolve())
        except OSError:
            continue
    uniq = []
    seen = set()
    for p in found:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    if uniq:
        uniq.sort(key=lambda p: (0 if p.name == "market_data_clean.db" else 1, -p.stat().st_size))
        return str(uniq[0])
    return str(_ROOT / "market_data_clean.db")


def _db_path() -> str:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = resolve_db_path()
        os.environ["MARKET_DB_PATH"] = _DB_PATH
        try:
            size = Path(_DB_PATH).stat().st_size if Path(_DB_PATH).exists() else 0
        except OSError:
            size = 0
        print(f"[db] using {_DB_PATH} ({size} bytes)", flush=True)
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = _db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
    return _conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol     TEXT    NOT NULL,
                timeframe  TEXT    NOT NULL,
                ts         INTEGER NOT NULL,
                open       REAL    NOT NULL,
                high       REAL    NOT NULL,
                low        REAL    NOT NULL,
                close      REAL    NOT NULL,
                volume     REAL    NOT NULL,
                PRIMARY KEY (symbol, timeframe, ts)
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(symbol, timeframe, ts);"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_meta (
                symbol      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                last_sync   INTEGER,
                last_status TEXT,
                PRIMARY KEY (symbol, timeframe)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_c_watch (
                thesis_id    TEXT PRIMARY KEY,
                direction    TEXT,
                origin_ts    INTEGER,
                origin_level REAL,
                m5_slot      INTEGER,
                status       TEXT NOT NULL,
                checked_ts   TEXT,
                started_at   REAL,
                updated_at   REAL NOT NULL,
                entry_ts     INTEGER,
                entry_price  REAL,
                reason       TEXT,
                window_open_ts INTEGER
            );
            """
        )
        try:
            conn.execute("ALTER TABLE scenario_c_watch ADD COLUMN window_open_ts INTEGER")
        except Exception:
            pass
        conn.commit()


def save_scenario_watch(thesis_id: str, **fields) -> None:
    """Persist one row of Case-1/scenario-entry state."""
    import time as _t
    cols = ["direction", "origin_ts", "origin_level", "m5_slot", "status",
            "checked_ts", "started_at", "entry_ts", "entry_price", "reason",
            "window_open_ts"]
    with _lock:
        conn = _connect()
        existing = conn.execute(
            "SELECT * FROM scenario_c_watch WHERE thesis_id=?", (thesis_id,)
        ).fetchone()
        merged = {c: fields.get(c, existing[c] if existing else None) for c in cols}
        conn.execute(
            """
            INSERT INTO scenario_c_watch
                (thesis_id, direction, origin_ts, origin_level, m5_slot, status,
                 checked_ts, started_at, updated_at, entry_ts, entry_price, reason,
                 window_open_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thesis_id) DO UPDATE SET
                direction=excluded.direction, origin_ts=excluded.origin_ts,
                origin_level=excluded.origin_level, m5_slot=excluded.m5_slot,
                status=excluded.status, checked_ts=excluded.checked_ts,
                started_at=excluded.started_at, updated_at=excluded.updated_at,
                entry_ts=excluded.entry_ts, entry_price=excluded.entry_price,
                reason=excluded.reason, window_open_ts=excluded.window_open_ts;
            """,
            (thesis_id, merged["direction"], merged["origin_ts"], merged["origin_level"],
             merged["m5_slot"], merged["status"], merged["checked_ts"], merged["started_at"],
             _t.time(), merged["entry_ts"], merged["entry_price"], merged["reason"],
             merged["window_open_ts"]),
        )
        conn.commit()


def load_all_scenario_watch() -> List[Dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT * FROM scenario_c_watch").fetchall()
    return [dict(r) for r in rows]


def upsert_candles(symbol: str, timeframe: str, candles: List[Dict]) -> int:
    if not candles:
        return 0
    rows = [
        (symbol, timeframe, int(c["ts"]), float(c["open"]), float(c["high"]),
         float(c["low"]), float(c["close"]), float(c["volume"]))
        for c in candles
    ]
    with _lock:
        conn = _connect()
        conn.executemany(
            """
            INSERT INTO candles(symbol, timeframe, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume;
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def get_candles(symbol: str, timeframe: str, limit: int = 1000) -> List[Dict]:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
            (symbol, timeframe, limit),
        )
        rows = cur.fetchall()
    rows = list(reversed(rows))
    return [dict(r) for r in rows]


def count_candles(symbol: str, timeframe: str) -> int:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT COUNT(*) AS c FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        )
        return int(cur.fetchone()["c"])


def bounds(symbol: str, timeframe: str):
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT MIN(ts) AS mn, MAX(ts) AS mx FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        )
        r = cur.fetchone()
        return (r["mn"], r["mx"])


def set_sync_meta(symbol: str, timeframe: str, ts: int, status: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO sync_meta(symbol, timeframe, last_sync, last_status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                last_sync=excluded.last_sync, last_status=excluded.last_status;
            """,
            (symbol, timeframe, ts, status),
        )
        conn.commit()


def get_sync_meta(symbol: str, timeframe: str):
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "SELECT last_sync, last_status FROM sync_meta WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        )
        r = cur.fetchone()
        return dict(r) if r else {"last_sync": None, "last_status": None}


def db_path() -> str:
    return _db_path()
