"""M15 setup tracking.

A "setup" covers one candle's lifetime on the auto-trade timeframe — it
opens when that candle starts, closes when the next candle begins.

The setup_id is derived DETERMINISTICALLY from the candle's own
timestamp, rather than from an incrementing counter kept in a separate
table. This means calling get_or_create_setup() twice for the same
candle always returns the exact same id — no risk of double-counting or
needing to coordinate a counter across the async loops that call into
this — and no extra state to keep in sync.

Format: SETUP-{YYYYMMDD}-{SYMBOL}-{TIMEFRAME}-{candle_ts}
(the spec's own example used a sequence number; a timestamp-derived id
serves the same "one unique id per candle" purpose without the added
complexity of a persistent counter, and is trivially reconstructible from
the candle alone.)
"""
import time
from typing import Dict, Optional

from . import db as logdb


def make_setup_id(symbol: str, timeframe: str, candle_ts: int) -> str:
    date_str = time.strftime("%Y%m%d", time.gmtime(candle_ts))
    return f"SETUP-{date_str}-{symbol}-{timeframe}-{candle_ts}"


def get_or_create_setup(symbol: str, timeframe: str, candle_ts: int, initial_state: str) -> str:
    setup_id = make_setup_id(symbol, timeframe, candle_ts)
    with logdb._conn() as conn:
        row = conn.execute("SELECT id FROM setups WHERE id=?", (setup_id,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO setups (id, symbol, timeframe, opened_at, initial_state, final_state)
                   VALUES (?,?,?,?,?,?)""",
                (setup_id, symbol, timeframe, candle_ts, initial_state, initial_state),
            )
            conn.commit()
    return setup_id


def close_previous_setup_if_needed(symbol: str, timeframe: str, previous_candle_ts: Optional[int],
                                    new_candle_ts: int) -> None:
    """Called when a NEW candle is detected — marks the PREVIOUS setup as
    closed (if one existed) and computes its summary (spec section 23)."""
    if previous_candle_ts is None or previous_candle_ts == new_candle_ts:
        return
    prev_id = make_setup_id(symbol, timeframe, previous_candle_ts)
    with logdb._conn() as conn:
        row = conn.execute("SELECT id, closed_at FROM setups WHERE id=?", (prev_id,)).fetchone()
        if row is None or row["closed_at"] is not None:
            return  # never opened, or already closed — nothing to do
        conn.execute("UPDATE setups SET closed_at=? WHERE id=?", (new_candle_ts, prev_id))
        conn.commit()


def update_setup_final_state(setup_id: str, final_state: str) -> None:
    with logdb._conn() as conn:
        conn.execute("UPDATE setups SET final_state=? WHERE id=?", (final_state, setup_id))
        conn.commit()


def increment_setup_counters(setup_id: str, meaningful_change: bool = False,
                              decision: bool = False, trade: bool = False,
                              rejected: bool = False, rejection_stage: Optional[str] = None,
                              confluence_score: Optional[float] = None) -> None:
    with logdb._conn() as conn:
        row = conn.execute("SELECT * FROM setups WHERE id=?", (setup_id,)).fetchone()
        if row is None:
            return
        updates = {}
        if meaningful_change:
            updates["meaningful_changes_count"] = row["meaningful_changes_count"] + 1
        if decision:
            updates["decisions_count"] = row["decisions_count"] + 1
        if trade:
            updates["trades_count"] = row["trades_count"] + 1
        if rejected:
            updates["rejected_count"] = row["rejected_count"] + 1
            if row["primary_rejection_stage"] is None and rejection_stage:
                updates["primary_rejection_stage"] = rejection_stage
        if confluence_score is not None:
            current_max = row["max_confluence"]
            if current_max is None or confluence_score > current_max:
                updates["max_confluence"] = confluence_score
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE setups SET {set_clause} WHERE id=?", (*updates.values(), setup_id))
        conn.commit()


def get_setup_summary(setup_id: str) -> Optional[Dict]:
    with logdb._conn() as conn:
        row = conn.execute("SELECT * FROM setups WHERE id=?", (setup_id,)).fetchone()
    return dict(row) if row else None