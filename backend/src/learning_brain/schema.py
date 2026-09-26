"""Learning Brain tables on the same SQLite file as candles."""
from __future__ import annotations

import sqlite3

from ..market_data import database as mdb


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(mdb.db_path())
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS lb_snapshots (
                obs_id TEXT PRIMARY KEY,
                thesis_id TEXT,
                hunt_version TEXT,
                timing_version TEXT,
                ts INTEGER NOT NULL,
                bar_ts_15m INTEGER,
                bar_ts_5m INTEGER,
                bar_ts_1m INTEGER,
                row_kind TEXT,
                source TEXT,
                action TEXT,
                direction TEXT,
                event TEXT,
                gate TEXT,
                timing TEXT,
                timing_state TEXT,
                slot INTEGER,
                weather TEXT,
                features_json TEXT NOT NULL,
                hunt_json TEXT,
                label_status TEXT DEFAULT 'OPEN',
                trade_id TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS lb_outcomes (
                obs_id TEXT PRIMARY KEY,
                trade_id TEXT,
                thesis_id TEXT,
                exit_ts INTEGER,
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL,
                r_multiple REAL,
                filled INTEGER DEFAULT 0
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS lb_models (
                model_id TEXT PRIMARY KEY,
                trained_at INTEGER NOT NULL,
                hunt_version TEXT,
                kind TEXT,
                metrics_json TEXT,
                artifact_path TEXT,
                role TEXT DEFAULT 'candidate',
                n_train INTEGER,
                n_test INTEGER
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_lb_snap_ts ON lb_snapshots(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_lb_snap_kind ON lb_snapshots(row_kind)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_lb_snap_thesis ON lb_snapshots(thesis_id)")
        c.commit()
