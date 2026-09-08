"""SQLite schema for the decision logging system.

Uses the same database file as everything else (market_data.database's
db_path()) — one file, one place to look, consistent with how
paper_trading/backtest_log/walkforward_log already work.

Tables match the spec's section 18 layout:
  setups, market_checkpoints, market_changes, decisions, agent_decisions,
  trade_executions, trade_events, trade_outcomes
"""
import sqlite3
from typing import Optional

from ..market_data import database as mdb


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(mdb.db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS setups (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER,
            initial_state TEXT,
            final_state TEXT,
            meaningful_changes_count INTEGER DEFAULT 0,
            decisions_count INTEGER DEFAULT 0,
            trades_count INTEGER DEFAULT 0,
            rejected_count INTEGER DEFAULT 0,
            primary_rejection_stage TEXT,
            max_confluence REAL,
            final_outcome TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS market_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            symbol TEXT, timeframe TEXT, price REAL,
            htf_regime TEXT, structure TEXT, momentum TEXT, breakout TEXT,
            status TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS market_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            field TEXT NOT NULL,
            previous_value TEXT,
            current_value TEXT,
            status TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT UNIQUE NOT NULL,
            setup_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            symbol TEXT, timeframe TEXT,
            current_price REAL, spread REAL, spread_pct REAL,
            atr REAL, atr_pct REAL, volatility_status TEXT, market_session TEXT,
            htf_structure TEXT, m15_structure TEXT, trend_state TEXT,
            momentum_state TEXT, breakout_state TEXT,
            htf_regime TEXT, htf_regime_score REAL, htf_direction TEXT,
            htf_confidence REAL, htf_blocked INTEGER, htf_blocked_reason TEXT,
            confluence_bullish_score REAL, confluence_bearish_score REAL,
            confluence_net_score REAL, confluence_confidence REAL,
            agents_passing INTEGER, agents_failing INTEGER, agents_neutral INTEGER,
            entry_filter_result TEXT, entry_filter_reason TEXT,
            risk_status TEXT, risk_score REAL, stop_distance REAL,
            risk_reward REAL, position_size_status TEXT,
            final_decision TEXT, confidence REAL,
            primary_reason TEXT, secondary_reason TEXT,
            rejection_stage TEXT, rejection_reason TEXT,
            explanation TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS agent_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            direction TEXT, score REAL, status TEXT, confidence REAL,
            reason TEXT, evidence_json TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS trade_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE NOT NULL,
            decision_id TEXT, setup_id TEXT,
            timestamp INTEGER NOT NULL,
            direction TEXT, entry_price REAL, position_size REAL,
            stop_loss REAL, take_profit REAL
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS trade_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            details_json TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS trade_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE NOT NULL,
            exit_price REAL, exit_time INTEGER, pnl REAL, r_multiple REAL,
            mfe REAL, mae REAL, exit_reason TEXT,
            original_thesis TEXT, thesis_result TEXT
        )""")

        # Indexes per spec section 18.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_setup ON market_checkpoints(setup_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_ts ON market_checkpoints(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_setup ON market_changes(setup_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_setup ON decisions(setup_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_final ON decisions(final_decision)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_rejstage ON decisions(rejection_stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agentdec_decision ON agent_decisions(decision_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tradeexec_decision ON trade_executions(decision_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tradeevents_trade ON trade_events(trade_id)")

        conn.commit()