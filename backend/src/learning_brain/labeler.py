"""Attach outcomes after a clip closes."""
from __future__ import annotations
from .isolation import fail_open
from .schema import conn, init

@fail_open(None)
def label_close(trade_id, exit_price, exit_reason, pnl, entry_price, sl_price, side=None):
    init()
    r = None
    try:
        if sl_price is not None and entry_price is not None:
            risk = abs(float(entry_price) - float(sl_price))
            if risk > 0:
                sign = -1.0 if str(side or "").upper() == "SHORT" else 1.0
                r = ((float(exit_price) - float(entry_price)) * sign) / risk
    except (TypeError, ValueError):
        r = None
    with conn() as c:
        row = c.execute("SELECT obs_id, thesis_id FROM lb_snapshots WHERE trade_id=? ORDER BY ts DESC LIMIT 1", (trade_id,)).fetchone()
        if row is None:
            row = c.execute("SELECT obs_id, thesis_id FROM lb_snapshots WHERE row_kind IN ('FIRE_SENT','FIRE_CANDIDATE') AND label_status='OPEN' ORDER BY ts DESC LIMIT 1").fetchone()
        if row is None:
            return
        c.execute("INSERT OR REPLACE INTO lb_outcomes (obs_id, trade_id, thesis_id, exit_ts, exit_price, exit_reason, pnl, r_multiple, filled) VALUES (?,?,?,?,?,?,?,?,1)", (row["obs_id"], trade_id, row["thesis_id"], __import__("time").time(), float(exit_price), str(exit_reason), float(pnl), r))
        c.execute("UPDATE lb_snapshots SET label_status='LABELED', trade_id=? WHERE obs_id=?", (trade_id, row["obs_id"]))
        c.commit()
