"""SQLite persistence for Swing AI (own tables in the app's market database; nothing else is touched)."""
import json
import time
from typing import Any, Dict, List, Optional

from ..market_data import database as db

TABLES = """
CREATE TABLE IF NOT EXISTS swing_ai_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, price REAL, json TEXT);
CREATE TABLE IF NOT EXISTS swing_ai_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, tf TEXT, detail TEXT, ekey TEXT);
CREATE TABLE IF NOT EXISTS swing_ai_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, wake TEXT, price REAL, snapshot_id INTEGER,
  decision TEXT, confidence REAL, entry REAL, sl REAL, tp REAL, thesis TEXT, invalidation TEXT, raw TEXT,
  valid INTEGER, risk_ok INTEGER, risk_reasons TEXT, position_id INTEGER, error TEXT, model TEXT, latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS swing_ai_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, side TEXT, entry_type TEXT, plan_entry REAL, fill_price REAL,
  sl REAL, sl0 REAL, tp REAL, qty REAL, risk_usd REAL, risk_dist REAL, created_ts INTEGER, opened_ts INTEGER,
  expires_ts INTEGER, thesis TEXT, invalidation TEXT, invalidation_price REAL, decision_id INTEGER,
  fee_entry REAL, fee_tp REAL, fee_sl REAL, mfe_r REAL DEFAULT 0, mae_r REAL DEFAULT 0, last_bar_ts INTEGER,
  exit_price REAL, exit_reason TEXT, closed_ts INTEGER, r_gross REAL, r_net REAL, fees_usd REAL, meta TEXT);
"""
_ready = False


def init() -> None:
    global _ready
    with db._lock:
        c = db._connect()
        c.executescript(TABLES)
        c.commit()
    _ready = True


def _ensure() -> None:
    if not _ready:
        init()


def _exec(sql: str, params: tuple = ()) -> int:
    _ensure()
    with db._lock:
        c = db._connect()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def _rows(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    _ensure()
    with db._lock:
        return [dict(r) for r in db._connect().execute(sql, params).fetchall()]


def add_snapshot(ts: int, price: float, snap: Dict[str, Any]) -> int:
    return _exec("INSERT INTO swing_ai_snapshots(ts,price,json) VALUES(?,?,?)", (ts, price, json.dumps(snap, separators=(",", ":"))))


def get_snapshot(sid: int) -> Optional[Dict[str, Any]]:
    r = _rows("SELECT json FROM swing_ai_snapshots WHERE id=?", (sid,))
    return json.loads(r[0]["json"]) if r else None


def add_event(ts: int, ev: Dict[str, Any]) -> int:
    return _exec("INSERT INTO swing_ai_events(ts,kind,tf,detail,ekey) VALUES(?,?,?,?,?)",
                 (ts, ev["kind"], ev.get("tf"), ev.get("detail"), ev.get("key")))


def add_decision(**k) -> int:
    cols = ["ts", "kind", "wake", "price", "snapshot_id", "decision", "confidence", "entry", "sl", "tp", "thesis",
            "invalidation", "raw", "valid", "risk_ok", "risk_reasons", "position_id", "error", "model", "latency_ms"]
    return _exec(f"INSERT INTO swing_ai_decisions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                 tuple(k.get(c) for c in cols))


def set_decision_position(did: int, pid: int) -> None:
    _exec("UPDATE swing_ai_decisions SET position_id=? WHERE id=?", (pid, did))


def add_position(p: Dict[str, Any]) -> int:
    cols = list(p.keys())
    return _exec(f"INSERT INTO swing_ai_positions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(p.values()))


def update_position(pid: int, **k) -> None:
    sets = ",".join(f"{c}=?" for c in k)
    _exec(f"UPDATE swing_ai_positions SET {sets} WHERE id=?", tuple(k.values()) + (pid,))


def get_position(pid: int) -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM swing_ai_positions WHERE id=?", (pid,))
    return r[0] if r else None


def active_position() -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM swing_ai_positions WHERE status IN ('PENDING','OPEN') ORDER BY id DESC LIMIT 1")
    return r[0] if r else None


def positions(status: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    if status:
        return _rows("SELECT * FROM swing_ai_positions WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
    return _rows("SELECT * FROM swing_ai_positions ORDER BY id DESC LIMIT ?", (limit,))


def decisions(limit: int = 200) -> List[Dict[str, Any]]:
    return _rows("SELECT * FROM swing_ai_decisions ORDER BY id DESC LIMIT ?", (limit,))


def day_stats(now: float) -> Dict[str, Any]:
    day0 = int(now // 86400 * 86400)
    opened = _rows("SELECT COUNT(*) n FROM swing_ai_positions WHERE opened_ts>=?", (day0,))[0]["n"]
    closed = _rows("SELECT r_net, closed_ts FROM swing_ai_positions WHERE status='CLOSED' AND closed_ts>=?", (day0,))
    last_loss = _rows("SELECT MAX(closed_ts) t FROM swing_ai_positions WHERE status='CLOSED' AND r_net<0")[0]["t"]
    return {"trades_today": opened, "daily_r": sum(r["r_net"] or 0 for r in closed), "last_loss_ts": last_loss}
