"""Persistent log of walk-forward (Full Simulation) backtest runs.

Same idea as backtest_log.py for the Quick Test tab, but for the real
trade-by-trade simulator: every finished run gets saved here so you can
compare settings/weight experiments over time instead of losing each
result the moment you close the modal or start another run.

Only summary numbers are stored, not the full trade list or equity
curve — those stay large and are only useful while looking at that one
specific run right after it finishes.
"""
import json
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
            """CREATE TABLE IF NOT EXISTS walkforward_runs (
                id                  TEXT PRIMARY KEY,
                created_at          INTEGER NOT NULL,
                timeframe           TEXT NOT NULL,
                days                REAL,
                start_balance       REAL,
                capital_pct         REAL,
                leverage            REAL,
                sl_atr_mult         REAL,
                tp_atr_mult         REAL,
                used_custom_weights INTEGER,
                weights_json        TEXT,
                used_custom_entry   INTEGER,
                entry_json          TEXT,
                used_custom_conflict INTEGER,
                conflict_json       TEXT,
                used_custom_htf_gate_values INTEGER,
                htf_gate_json       TEXT,
                htf_timeframes_json TEXT,
                use_htf_gate        INTEGER,
                pivot_window_forced INTEGER,
                actual_days_tested  REAL,
                data_shortfall      INTEGER,
                trades              INTEGER,
                win_rate            REAL,
                profit_factor       REAL,
                avg_r               REAL,
                net_pnl             REAL,
                return_pct          REAL,
                final_balance       REAL,
                note                TEXT
            );"""
        )
        # Migration for databases created before entry/conflict/htf_gate
        # override testing existed — CREATE TABLE IF NOT EXISTS only
        # applies the full schema to a brand-new table, so any run logged
        # before this update needs these columns added after the fact.
        # Existing rows simply get NULL/empty for these (correctly — they
        # genuinely didn't test any of these, so there's nothing to
        # backfill).
        for col, coltype in [
            ("used_custom_entry", "INTEGER"), ("entry_json", "TEXT"),
            ("used_custom_conflict", "INTEGER"), ("conflict_json", "TEXT"),
            ("used_custom_htf_gate_values", "INTEGER"), ("htf_gate_json", "TEXT"),
            ("pivot_window_forced", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE walkforward_runs ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()


def record(result: Dict) -> Dict:
    """Pass in the full result dict returned by a finished
    WalkForwardBacktest run (the same thing the API's result endpoint
    returns)."""
    init_db()
    rid = str(uuid.uuid4())
    cfg = result.get("config", {})
    stats = result.get("stats", {})
    row = {
        "id": rid,
        "created_at": int(time.time()),
        "timeframe": result.get("timeframe"),
        "days": cfg.get("days"),
        "start_balance": cfg.get("start_balance"),
        "capital_pct": cfg.get("capital_pct"),
        "leverage": cfg.get("leverage"),
        "sl_atr_mult": cfg.get("sl_atr_mult"),
        "tp_atr_mult": cfg.get("tp_atr_mult"),
        "used_custom_weights": 1 if cfg.get("used_custom_weights") else 0,
        "weights_json": json.dumps(result.get("weights_used", {})),
        "used_custom_entry": 1 if cfg.get("used_custom_entry") else 0,
        "entry_json": json.dumps(result.get("entry_used", {})),
        "used_custom_conflict": 1 if cfg.get("used_custom_conflict") else 0,
        "conflict_json": json.dumps(result.get("conflict_used", {})),
        "used_custom_htf_gate_values": 1 if cfg.get("used_custom_htf_gate_values") else 0,
        "htf_gate_json": json.dumps(result.get("htf_gate_used", {})),
        "htf_timeframes_json": json.dumps(cfg.get("htf_timeframes", [])),
        "use_htf_gate": 1 if cfg.get("use_htf_gate", True) else 0,
        "pivot_window_forced": cfg.get("pivot_window_forced"),  # None = dynamic default
        "actual_days_tested": result.get("actual_days_tested"),
        "data_shortfall": 1 if result.get("data_shortfall") else 0,
        "trades": stats.get("trades"),
        "win_rate": stats.get("win_rate"),
        "profit_factor": stats.get("profit_factor"),
        "avg_r": stats.get("avg_r"),
        "net_pnl": stats.get("net_pnl"),
        "return_pct": stats.get("return_pct"),
        "final_balance": result.get("final_balance"),
        "note": "",
    }
    with _conn() as conn:
        conn.execute(
            """INSERT INTO walkforward_runs
               (id, created_at, timeframe, days, start_balance, capital_pct,
                leverage, sl_atr_mult, tp_atr_mult, used_custom_weights,
                weights_json, used_custom_entry, entry_json,
                used_custom_conflict, conflict_json,
                used_custom_htf_gate_values, htf_gate_json,
                htf_timeframes_json, use_htf_gate, pivot_window_forced,
                actual_days_tested, data_shortfall, trades,
                win_rate, profit_factor, avg_r, net_pnl, return_pct,
                final_balance, note)
               VALUES (:id, :created_at, :timeframe, :days, :start_balance,
                       :capital_pct, :leverage, :sl_atr_mult, :tp_atr_mult,
                       :used_custom_weights, :weights_json, :used_custom_entry,
                       :entry_json, :used_custom_conflict, :conflict_json,
                       :used_custom_htf_gate_values, :htf_gate_json,
                       :htf_timeframes_json, :use_htf_gate, :pivot_window_forced,
                       :actual_days_tested, :data_shortfall,
                       :trades, :win_rate, :profit_factor, :avg_r,
                       :net_pnl, :return_pct, :final_balance, :note)""",
            row,
        )
        conn.commit()
    return row


def list_runs(limit: int = 100) -> List[Dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM walkforward_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["weights_used"] = json.loads(d.pop("weights_json") or "{}")
        except Exception:
            d["weights_used"] = {}
        try:
            d["entry_used"] = json.loads(d.pop("entry_json") or "{}")
        except Exception:
            d["entry_used"] = {}
        try:
            d["conflict_used"] = json.loads(d.pop("conflict_json") or "{}")
        except Exception:
            d["conflict_used"] = {}
        try:
            d["htf_gate_used"] = json.loads(d.pop("htf_gate_json") or "{}")
        except Exception:
            d["htf_gate_used"] = {}
        try:
            d["htf_timeframes"] = json.loads(d.pop("htf_timeframes_json") or "[]")
        except Exception:
            d["htf_timeframes"] = []
        out.append(d)
    return out


def delete_run(run_id: str) -> bool:
    init_db()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM walkforward_runs WHERE id=?", (run_id,))
        conn.commit()
        return cur.rowcount > 0


def clear() -> int:
    init_db()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM walkforward_runs")
        conn.commit()
        return cur.rowcount