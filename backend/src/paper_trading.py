"""Paper trading engine with persistent SQLite history.

Live AUTO shadows (thesis.venue == MEXC) show MEXC's real fill / mark / uPnL
while open, and MEXC history fill / exit / realised after close.
Hunt SL/TP prices stay Hunt's.
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
        for stmt in (
            "ALTER TABLE paper_trades ADD COLUMN source TEXT DEFAULT 'MANUAL'",
            "ALTER TABLE paper_trades ADD COLUMN decision_id TEXT",
            "ALTER TABLE paper_trades ADD COLUMN thesis_json TEXT",
            "ALTER TABLE paper_trades ADD COLUMN thesis_result TEXT",
        ):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        conn.commit()


def _row_to_dict(r: sqlite3.Row) -> Dict:
    d = {k: r[k] for k in r.keys()}
    d["thesis"] = _thesis(d)
    return d


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


def _is_mexc_live(t: Dict) -> bool:
    if t.get("source") != "AUTO":
        return False
    th = _thesis(t)
    if th.get("venue") == "MEXC":
        return True
    try:
        return float(t.get("notional_usd") or 0) < 200
    except (TypeError, ValueError):
        return False


def _f(p: Dict, *keys):
    for k in keys:
        if p.get(k) is not None and p.get(k) != "":
            try:
                return float(p.get(k))
            except (TypeError, ValueError):
                continue
    return None


def _mexc_open_snapshot(symbol: str) -> Optional[Dict]:
    try:
        from .market_data import mexc_private
        rows = mexc_private.get_open_positions(symbol) or []
    except Exception:
        return None
    if not rows:
        return None
    p = rows[0]
    avg = _f(p, "holdAvgPrice", "openAvgPrice", "avgEntryPrice", "openPrice")
    mark = _f(p, "fairPrice", "markPrice", "newOpenAvgPrice")
    upnl = _f(p, "unrealised", "unrealisedPnl", "unrealizedPnl", "unrealisedProfit", "pnl")
    vol = _f(p, "holdVol", "hold_vol", "volume")
    margin = _f(p, "im", "oim", "positionMargin", "holdMargin")
    if not avg:
        return None
    return {"entry": avg, "mark": mark, "upnl": upnl, "vol": vol, "margin": margin, "raw": p}


def _mexc_history_rows(symbol: str) -> List[Dict]:
    try:
        from .market_data import mexc_private
        return mexc_private.get_history_positions(symbol=symbol, page_num=1, page_size=50) or []
    except Exception:
        return []


def _match_history(t: Dict, rows: List[Dict]) -> Optional[Dict]:
    want = 1 if t.get("side") == LONG else 2
    opened = float(t.get("opened_at") or 0)
    closed = float(t.get("closed_at") or 0)
    best = None
    best_dt = 1e18
    for p in rows:
        try:
            pt = int(p.get("positionType") or 0)
        except (TypeError, ValueError):
            continue
        if pt and pt != want:
            continue
        sym = p.get("symbol") or ""
        if sym and t.get("symbol") and sym != t.get("symbol"):
            continue
        created = _f(p, "createTime") or 0
        if created > 1e12:
            created = created / 1000.0
        updated = _f(p, "updateTime") or 0
        if updated > 1e12:
            updated = updated / 1000.0
        dt = abs(created - opened) if opened else 1e18
        if closed and updated:
            dt = min(dt, abs(updated - closed))
        if dt < best_dt and dt < 20 * 60:
            best = p
            best_dt = dt
    return best


def apply_mexc_close(tid: str, entry: Optional[float], exit_price: Optional[float],
                     pnl: Optional[float], pnl_pct: Optional[float]) -> Optional[Dict]:
    t = get_trade(tid)
    if not t:
        return t
    fields = []
    args = []
    if entry is not None:
        fields.append("entry_price=?")
        args.append(float(entry))
    if exit_price is not None:
        fields.append("exit_price=?")
        args.append(float(exit_price))
    if pnl is not None:
        fields.append("pnl=?")
        args.append(float(pnl))
    if pnl_pct is not None:
        fields.append("pnl_pct=?")
        args.append(float(pnl_pct))
    if not fields:
        return t
    args.append(tid)
    with _lock:
        conn = _connect()
        conn.execute(f"UPDATE paper_trades SET {', '.join(fields)} WHERE id=?", args)
        conn.commit()
    return get_trade(tid)


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
    if t.get("status") != "OPEN" or not _is_mexc_live(t):
        return t
    snap = _mexc_open_snapshot(t.get("symbol") or "BTC_USDT")
    if not snap:
        return t
    avg = snap["entry"]
    mark = snap["mark"]
    upnl = snap["upnl"]
    vol = snap["vol"]
    margin = snap["margin"]
    stored_entry = float(t.get("entry_price") or 0)
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
        if abs(stored_entry - avg) > 0.05:
            qty = t.get("qty")
            notion = t.get("notional_usd")
            cs = None
            try:
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


def _overlay_mexc_closed(t: Dict, rows: List[Dict]) -> Dict:
    if t.get("status") != "CLOSED" or not _is_mexc_live(t):
        return t
    p = _match_history(t, rows)
    if not p:
        return t
    entry = _f(p, "openAvgPrice", "newOpenAvgPrice", "holdAvgPrice")
    exit_px = _f(p, "closeAvgPrice", "newCloseAvgPrice")
    gross = _f(p, "closeProfitLoss")
    fee = _f(p, "fee", "totalFee")
    if fee is not None:
        fee = abs(fee)
    pnl = _f(p, "realised")
    if pnl is None and gross is not None:
        pnl = gross - (fee or 0)
    ratio = _f(p, "profitRatio")
    pct = (ratio * 100.0) if ratio is not None else None
    if pct is None and entry and exit_px:
        _, pct = _pnl(t["side"], entry, exit_px, t.get("qty") or 1)
    if entry:
        t["entry_price"] = entry
    if exit_px:
        t["exit_price"] = exit_px
    if gross is not None:
        t["gross_pnl"] = round(gross, 4)
    if fee is not None:
        t["fee"] = round(fee, 4)
    if pnl is not None:
        t["pnl"] = round(pnl, 4)
    if pct is not None:
        t["pnl_pct"] = round(pct, 3)
    try:
        apply_mexc_close(t["id"], entry, exit_px, pnl, pct)
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
    closed = get_trade(tid)
    if closed and _is_mexc_live(closed):
        try:
            rows = _mexc_history_rows(closed.get("symbol") or "BTC_USDT")
            _overlay_mexc_closed(closed, rows)
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
            # Any position closing (SL or TP) changes the real account
            # balance -- force the next trade's sizing to re-fetch fresh
            # rather than reuse a now-stale normal_base snapshot. Local
            # import: avoids a top-level circular import with
            # autotrader_state (which itself locally imports this module).
            try:
                from .autotrader_state import STATE as _STATE
                _STATE["normal_base"] = None
                _STATE["normal_base_captured_at"] = None
            except Exception:
                pass
    return closed


def list_trades(status: Optional[str] = None, live_price: Optional[float] = None) -> List[Dict]:
    with _lock:
        conn = _connect()
        if status in ("OPEN", "CLOSED"):
            rows = conn.execute("SELECT * FROM paper_trades WHERE status=? ORDER BY opened_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM paper_trades ORDER BY opened_at DESC").fetchall()
    hist = None
    out = []
    for r in rows:
        t = _row_to_dict(r)
        if t["status"] == "OPEN" and live_price is not None:
            upnl, upct = _pnl(t["side"], t["entry_price"], float(live_price), t["qty"])
            t["unrealized_pnl"] = upnl
            t["unrealized_pnl_pct"] = upct
            t["mark_price"] = round(float(live_price), 2)
        t = _overlay_mexc_live(t)
        if t.get("status") == "CLOSED" and _is_mexc_live(t):
            if hist is None:
                hist = _mexc_history_rows(t.get("symbol") or "BTC_USDT")
            t = _overlay_mexc_closed(t, hist)
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