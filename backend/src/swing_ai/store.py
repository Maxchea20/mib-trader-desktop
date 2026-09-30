"""SQLite persistence for Swing AI - the audit trail of what GPT saw and what GPT decided.

  swing_ai_market_snapshots  the exact raw MEXC data package sent to GPT (zlib-compressed JSON)
  swing_ai_decisions         GPT's structured output, verbatim fields, plus the wake reason and the safety-layer verdict
  swing_ai_trades            paper trades with fees, MFE, MAE, R and final outcome
  swing_ai_wakes             the raw wake conditions that triggered reviews
"""
import json
import zlib
from typing import Any, Dict, List, Optional

from ..market_data import database as db

TABLES = """
CREATE TABLE IF NOT EXISTS swing_ai_market_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, symbol TEXT, price REAL, encoding TEXT, bytes INTEGER, payload BLOB);
CREATE TABLE IF NOT EXISTS swing_ai_wakes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, detail TEXT, wkey TEXT);
CREATE TABLE IF NOT EXISTS swing_ai_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, symbol TEXT, kind TEXT, wake_kind TEXT, wake_detail TEXT, price REAL,
  snapshot_id INTEGER, model TEXT, prompt_version TEXT, decision TEXT, confidence REAL, headline TEXT, market_state TEXT,
  daily_analysis TEXT, h4_analysis TEXT, h1_analysis TEXT, m15_analysis TEXT, structure_analysis TEXT, entry_analysis TEXT,
  entry_type TEXT, entry REAL, sl REAL, tp REAL, thesis TEXT, invalidation TEXT, invalidation_price REAL, wake_levels TEXT,
  raw TEXT, valid INTEGER, risk_ok INTEGER, risk_reasons TEXT, trade_id INTEGER, error TEXT, latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS swing_ai_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, status TEXT, side TEXT, entry_type TEXT, plan_entry REAL, fill_price REAL,
  sl REAL, sl0 REAL, tp REAL, qty REAL, risk_usd REAL, risk_dist REAL, created_ts INTEGER, opened_ts INTEGER, expires_ts INTEGER,
  thesis TEXT, invalidation TEXT, invalidation_price REAL, decision_id INTEGER, market_state TEXT, confidence REAL,
  fee_entry REAL, fee_tp REAL, fee_sl REAL, mfe_r REAL DEFAULT 0, mae_r REAL DEFAULT 0, last_bar_ts INTEGER,
  exit_price REAL, exit_reason TEXT, closed_ts INTEGER, r_gross REAL, r_net REAL, fees_usd REAL, outcome TEXT, meta TEXT);
"""
_ready = False
ENCODING = "zlib+json"


def init() -> None:
    global _ready
    with db._lock:
        c = db._connect()
        c.executescript(TABLES)
        cols = {r[1] for r in c.execute("PRAGMA table_info(swing_ai_decisions)").fetchall()}
        if "headline" not in cols:                          # databases created before the headline field existed
            c.execute("ALTER TABLE swing_ai_decisions ADD COLUMN headline TEXT")
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


# --- raw market snapshots -------------------------------------------------------------------
def add_snapshot(ts: int, symbol: str, price: float, snap: Dict[str, Any]) -> int:
    blob = zlib.compress(json.dumps(snap, separators=(",", ":")).encode("utf-8"), 6)
    return _exec("INSERT INTO swing_ai_market_snapshots(ts,symbol,price,encoding,bytes,payload) VALUES(?,?,?,?,?,?)",
                 (ts, symbol, price, ENCODING, len(blob), blob))


def get_snapshot(sid: int) -> Optional[Dict[str, Any]]:
    _ensure()
    with db._lock:
        r = db._connect().execute("SELECT payload FROM swing_ai_market_snapshots WHERE id=?", (sid,)).fetchone()
    return json.loads(zlib.decompress(r["payload"]).decode("utf-8")) if r else None


# --- wakes ------------------------------------------------------------------------------------
def add_wake(ts: int, w: Dict[str, Any]) -> int:
    return _exec("INSERT INTO swing_ai_wakes(ts,kind,detail,wkey) VALUES(?,?,?,?)", (ts, w["kind"], w.get("detail"), w.get("key")))


# --- decisions --------------------------------------------------------------------------------
DECISION_COLS = ["ts", "symbol", "kind", "wake_kind", "wake_detail", "price", "snapshot_id", "model", "prompt_version", "decision",
                 "confidence", "headline", "market_state", "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis", "structure_analysis",
                 "entry_analysis", "entry_type", "entry", "sl", "tp", "thesis", "invalidation", "invalidation_price", "wake_levels",
                 "raw", "valid", "risk_ok", "risk_reasons", "trade_id", "error", "latency_ms"]


def add_decision(**k) -> int:
    if isinstance(k.get("wake_levels"), (list, dict)):
        k["wake_levels"] = json.dumps(k["wake_levels"])
    return _exec(f"INSERT INTO swing_ai_decisions({','.join(DECISION_COLS)}) VALUES({','.join('?' * len(DECISION_COLS))})",
                 tuple(k.get(c) for c in DECISION_COLS))


def set_decision_trade(did: int, tid: int) -> None:
    _exec("UPDATE swing_ai_decisions SET trade_id=? WHERE id=?", (tid, did))


def decisions(limit: int = 200) -> List[Dict[str, Any]]:
    return _rows("SELECT * FROM swing_ai_decisions ORDER BY id DESC LIMIT ?", (limit,))


def latest_entry_decision() -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM swing_ai_decisions WHERE kind='ENTRY' AND valid=1 ORDER BY id DESC LIMIT 1")
    return r[0] if r else None


def latest_decision() -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM swing_ai_decisions ORDER BY id DESC LIMIT 1")
    return r[0] if r else None


# --- trades -----------------------------------------------------------------------------------
def add_trade(p: Dict[str, Any]) -> int:
    cols = list(p.keys())
    return _exec(f"INSERT INTO swing_ai_trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(p.values()))


def update_trade(tid: int, **k) -> None:
    sets = ",".join(f"{c}=?" for c in k)
    _exec(f"UPDATE swing_ai_trades SET {sets} WHERE id=?", tuple(k.values()) + (tid,))


def get_trade(tid: int) -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM swing_ai_trades WHERE id=?", (tid,))
    return r[0] if r else None


def active_trade() -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM swing_ai_trades WHERE status IN ('PENDING','OPEN') ORDER BY id DESC LIMIT 1")
    return r[0] if r else None


def trades(status: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    if status:
        return _rows("SELECT * FROM swing_ai_trades WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
    return _rows("SELECT * FROM swing_ai_trades ORDER BY id DESC LIMIT ?", (limit,))


def day_stats(now: float) -> Dict[str, Any]:
    day0 = int(now // 86400 * 86400)
    opened = _rows("SELECT COUNT(*) n FROM swing_ai_trades WHERE opened_ts>=?", (day0,))[0]["n"]
    closed = _rows("SELECT r_net FROM swing_ai_trades WHERE status='CLOSED' AND closed_ts>=?", (day0,))
    last_loss = _rows("SELECT MAX(closed_ts) t FROM swing_ai_trades WHERE status='CLOSED' AND r_net<0")[0]["t"]
    return {"trades_today": opened, "daily_r": sum(r["r_net"] or 0 for r in closed), "last_loss_ts": last_loss}
