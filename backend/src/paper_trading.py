"""Paper trading engine with persistent SQLite history.

Live AUTO shadows (thesis.venue == MEXC) show MEXC's real fill / mark / uPnL
instead of Hunt's planned 15m level. Hunt SL/TP prices stay Hunt's.
"""
import time
import uuid
import sqlite3
import threading
import json
from typing import Dict, List, Optional

from .market_data import database as mdb

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

LONG = "LONG"
SHORT = "SHORT"


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(mdb.db_path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
    return _conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                id            TEXT PRIMARY KEY,
                symbol        TEXT NOT NULL,
                timeframe     TEXT,
                side          TEXT NOT NULL,
                status        TEXT NOT NULL,
                entry_price   REAL NOT NULL,
                sl_price      REAL,
                tp_price      REAL,
                qty           REAL NOT NULL,
                notional_usd  REAL NOT NULL,
                opened_at     INTEGER NOT NULL,
                closed_at     INTEGER,
                exit_price    REAL,
                exit_reason   TEXT,
                pnl           REAL,
                pnl_pct       REAL,
                brain_state   TEXT,
                consensus     REAL,
                confidence    REAL,
                note          TEXT,
                source        TEXT DEFAULT 'MANUAL'
            );
            """
        )
        try:
            conn.execute("ALTER TABLE paper_trades ADD COLUMN source TEXT DEFAULT 'MANUAL'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE paper_trades ADD COLUMN decision_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE paper_trades ADD COLUMN thesis_json TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE paper_trades ADD COLUMN thesis_result TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def _row_to_dict(r: sqlite3.Row) -> Dict:
    return {k: r[k] for k in r.keys()}


def _pnl(side: str, entry: float, exit_price: float, qty: float):
    if side == LONG:
        pnl = (exit_price - entry) * qty
        pct = (exit_price - entry) / entry * 100.0 if entry else 0.0
    else:
        pnl = (entry - exit_price) * qty
        pct = (entry - exit_price) / entry * 100.0 if entry else 0.0
    return round(pnl, 2), round(pct, 3)


def _thesis(t: Dict) -> Dict:
    raw = t.get("thesis_json") or t.get("thesis")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mexc_open_snapshot(symbol: str) -> Optional[Dict]:
    try:
        from .market_data import mexc_private
        rows = mexc_private.get_open_positions(symbol) or []
    except Exception:
        return None
    if not rows:
        return None
    p = rows[0]
    def f(*keys):
        for k in keys:
            if p.get(k) is not None and p.get(k) != "":
                try:
                    return float(p.get(k))
                except (TypeError, ValueError):
                    continue
        return None
    avg = f("holdAvgPrice", "openAvgPrice", "avgEntryPrice", "openPrice")
    mark = f("fairPrice", "markPrice", "newOpenAvgPrice")
    upnl = f("unrealised", "unrealisedPnl", "unrealizedPnl", "unrealisedProfit")
    vol = f("holdVol", "hold_vol", "volume")
    margin = f("im", "oim", "positionMargin", "holdMargin")
    if not avg:
        return None
    return {
        "entry": avg,
        "mark": mark,
        "upnl": upnl,
        "vol": vol,
        "margin": margin,
        "raw": p,
    }


def update_open_fill(tid: str, entry_price: float, qty: Optional[float] = None,
                     notional_usd: Optional[float] = None) -> Optional[Dict]:
    t = get_trade(tid)
    if not t or t["status"] != "OPEN":
        return t
    fields = ["entry_price=?"]
    args = [float(entry_price)]
    if qty is not None:
        fields.append("qty=?")
        args.append(float(qty))
    if notional_usd is not None:
        fields.append("notional_usd=?")
        args.append(float(notional_usd))
    args.append(tid)
    with _lock:
        conn = _connect()
        conn.execute(
            f"UPDATE paper_trades SET {', '.join(fields)} WHERE id=? AND status='OPEN'",
            args,
        )
        conn.commit()
    return get_trade(tid)


def _overlay_mexc_live(t: Dict) -> Dict:
    """Replace Hunt planned entry / local mark PnL with MEXC's live ticket."""
    if t.get("status") != "OPEN" or t.get("source") != "AUTO":
        return t
    th = _thesis(t)
    if th.get("venue") and th.get("venue") != "MEXC":
        return t
    snap = _mexc_open_snapshot(t.get("symbol") or "BTC_USDT")
    if not snap:
        return t
    avg = snap["entry"]
    mark = snap["mark"]
    upnl = snap["upnl"]
    vol = snap["vol"]
    margin = snap["margin"]
    t["entry_price"] = avg
    if mark:
        t["mark_price"] = round(mark, 1)
    if upnl is not None:
        t["unrealized_pnl"] = round(upnl, 4)
        if margin and margin > 0:
            t["unrealized_pnl_pct"] = round(upnl / margin * 100.0, 2)
        elif avg and mark and t.get("qty"):
            _, pct = _pnl(t["side"], avg, mark, t["qty"])
            t["unrealized_pnl_pct"] = pct
    elif mark:
        upnl2, pct = _pnl(t["side"], avg, mark, t.get("qty") or 0)
        t["unrealized_pnl"] = upnl2
        t["unrealized_pnl_pct"] = pct
    try:
        stored = float(t.get("entry_price") or 0)
        if abs(stored - avg) > 0.05:
            qty = t.get("qty")
            notion = t.get("notional_usd")
            cs = None
            try:
                from .config import SYMBOL as _sym  # noqa: F401
                from .market_data import mexc_market_data as mkt
                detail = mkt.rest_get(f"/api/v1/contract/detail?symbol={t.get('symbol') or 'BTC_USDT'}")
                d = (detail or {}).get("data") or {}
                if isinstance(d, list):
                    d = d[0] if d else {}
                cs = float(d.get("contractSize") or 0) or None
            except Exception:
                cs = None
            if vol and cs:
                qty = vol * cs
                notion = vol * cs * avg
            update_open_fill(t["id"], avg, qty=qty, notional_usd=notion)
            t["qty"] = qty if qty is not None else t.get("qty")
            t["notional_usd"] = notion if notion is not None else t.get("notional_usd")
    except Exception:
        pass
    return t


def open_trade(symbol: str, side: str, entry_price: float, sl_price: Optional[float],
               tp_price: Optional[float], notional_usd: float, timeframe: str = "",
               brain_state: str = "", consensus: float = 0.0, confidence: float = 0.0,
               note: str = "", source: str = "MANUAL", decision_id: Optional[str] = None,
               thesis: Optional[Dict] = None) -> Dict:
    side = LONG if str(side).upper() == LONG else SHORT
    entry_price = float(entry_price)
    notional_usd = max(1.0, float(notional_usd))
    if sl_price is None:
        sl_price = entry_price * (0.99 if side == LONG else 1.01)
    if tp_price is None:
        tp_price = entry_price * (1.02 if side == LONG else 0.98)
    qty = notional_usd / entry_price
    tid = str(uuid.uuid4())
    now = int(time.time())
    thesis_json = json.dumps(thesis) if thesis else None
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO paper_trades
               (id, symbol, timeframe, side, status, entry_price, sl_price, tp_price,
                qty, notional_usd, opened_at, brain_state, consensus, confidence, note, source, decision_id, thesis_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, symbol, timeframe, side, "OPEN", entry_price, float(sl_price),
             float(tp_price), qty, notional_usd, now, brain_state, float(consensus),
             float(confidence), note, source, decision_id, thesis_json),
        )
        conn.commit()
    try:
        from . import decision_log
        decision_log.logger.record_trade_execution(
            trade_id=tid, decision_id=decision_id, setup_id=None,
            direction=side, entry_price=entry_price, position_size=notional_usd,
            stop_loss=float(sl_price), take_profit=float(tp_price),
        )
    except Exception:
        pass
    return get_trade(tid)


def get_trade(tid: str) -> Optional[Dict]:
    with _lock:
        conn = _connect()
        r = conn.execute("SELECT * FROM paper_trades WHERE id=?", (tid,)).fetchone()
    return _row_to_dict(r) if r else None


def get_thesis(tid: str) -> Optional[Dict]:
    t = get_trade(tid)
    if not t or not t.get("thesis_json"):
        return None
    try:
        return json.loads(t["thesis_json"])
    except (TypeError, ValueError):
        return None


def _classify_thesis_result(exit_reason: str, pnl: float, had_thesis: bool) -> str:
    if not had_thesis:
        return "OTHER"
    win = pnl > 0
    if exit_reason == "TP":
        return "CONFIRMED"
    if exit_reason == "BRAIN_EXIT":
        return "INVALIDATED"
    if exit_reason == "SL":
        return "INVALIDATED" if not win else "PARTIALLY_CONFIRMED"
    if exit_reason == "FLIP":
        return "PARTIALLY_CONFIRMED" if win else "INVALIDATED"
    return "OTHER"


def update_sl(tid: str, sl_price: float) -> Optional[Dict]:
    t = get_trade(tid)
    if not t or t["status"] != "OPEN":
        return t
    with _lock:
        conn = _connect()
        conn.execute("UPDATE paper_trades SET sl_price=? WHERE id=? AND status='OPEN'", (float(sl_price), tid))
        conn.commit()
    return get_trade(tid)


def close_trade(tid: str, exit_price: float, reason: str = "MANUAL") -> Optional[Dict]:
    t = get_trade(tid)
    if not t or t["status"] != "OPEN":
        return t
    exit_price = float(exit_price)
    pnl, pct = _pnl(t["side"], t["entry_price"], exit_price, t["qty"])
    thesis_result = _classify_thesis_result(reason, pnl, bool(t.get("thesis_json")))
    with _lock:
        conn = _connect()
        conn.execute(
            """UPDATE paper_trades SET status='CLOSED', closed_at=?, exit_price=?,
               exit_reason=?, pnl=?, pnl_pct=?, thesis_result=? WHERE id=?""",
            (int(time.time()), exit_price, reason, pnl, pct, thesis_result, tid),
        )
        conn.commit()
    try:
        from . import decision_log
        decision_log.logger.record_trade_outcome(
            trade_id=tid, exit_price=exit_price, pnl=pnl, exit_reason=reason,
            entry_price=t["entry_price"], sl_price=t.get("sl_price"),
        )
    except Exception:
        pass
    return get_trade(tid)


def check_open_trades(price: Optional[float]) -> int:
    if price is None:
        return 0
    closed = 0
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall()
    for r in rows:
        t = _row_to_dict(r)
        side, sl, tp = t["side"], t["sl_price"], t["tp_price"]
        hit = None
        if side == LONG:
            if sl is not None and price <= sl:
                hit = ("SL", sl)
            elif tp is not None and price >= tp:
                hit = ("TP", tp)
        else:
            if sl is not None and price >= sl:
                hit = ("SL", sl)
            elif tp is not None and price <= tp:
                hit = ("TP", tp)
        if hit:
            close_trade(t["id"], hit[1], hit[0])
            closed += 1
    return closed


def list_trades(status: Optional[str] = None, live_price: Optional[float] = None) -> List[Dict]:
    with _lock:
        conn = _connect()
        if status in ("OPEN", "CLOSED"):
            rows = conn.execute("SELECT * FROM paper_trades WHERE status=? ORDER BY opened_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM paper_trades ORDER BY opened_at DESC").fetchall()
    out = []
    for r in rows:
        t = _row_to_dict(r)
        if t["status"] == "OPEN" and live_price is not None:
            upnl, upct = _pnl(t["side"], t["entry_price"], float(live_price), t["qty"])
            t["unrealized_pnl"] = upnl
            t["unrealized_pnl_pct"] = upct
            t["mark_price"] = round(float(live_price), 2)
        t = _overlay_mexc_live(t)
        out.append(t)
    return out


def stats() -> Dict:
    with _lock:
        conn = _connect()
        rows = conn.execute("SELECT * FROM paper_trades WHERE status='CLOSED'").fetchall()
        open_count = conn.execute("SELECT COUNT(*) c FROM paper_trades WHERE status='OPEN'").fetchone()["c"]
    closed = [_row_to_dict(r) for r in rows]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    losses = [t for t in closed if (t["pnl"] or 0) <= 0]
    total_pnl = round(sum(t["pnl"] or 0 for t in closed), 2)
    total_pct = round(sum(t["pnl_pct"] or 0 for t in closed), 3)
    win_rate = round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0
    best = max((t["pnl"] or 0 for t in closed), default=0.0)
    worst = min((t["pnl"] or 0 for t in closed), default=0.0)
    return {
        "total_trades": len(closed) + open_count,
        "open_trades": open_count,
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "total_realized_pnl": total_pnl,
        "total_realized_pnl_pct": total_pct,
        "best_trade": round(best, 2),
        "worst_trade": round(worst, 2),
    }
