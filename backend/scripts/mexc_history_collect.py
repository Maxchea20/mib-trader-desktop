#!/usr/bin/env python3
"""READ-ONLY MEXC trade-history collector + actual-trade ledger.

Pulls the complete MEXC futures history for the account and joins it to
MiB's own records using real MEXC IDs:

  MiB submit (live_sizing_log.jsonl) -> MEXC order -> fills -> position
  -> TP/SL stop order -> exit -> realised P&L / fees
  + paper_trades shadow row (thesis, gate, hunt entry)

SAFETY
  * Only GET requests are ever sent to MEXC (enforced in _get()).
    Nothing is placed, cancelled or changed.
  * market_data_clean.db and live_sizing_log.jsonl are opened read-only.
  * Output goes to backend/data/mexc_history/, which gets its own
    .gitignore ("*") so the private data can never be committed.

Run from the backend folder (same .env / API keys as the app):

  python scripts/mexc_history_collect.py
  python scripts/mexc_history_collect.py --since 2026-09-01
  python scripts/mexc_history_collect.py --replay tp_research_result.json
  python scripts/mexc_history_collect.py --offline   # re-join stored data, no MEXC calls

Outputs (backend/data/mexc_history/):
  mexc_history.db        raw MEXC rows (positions, orders, deals, stop orders) + fetch log
  ledger.csv / .json     one row per MiB-submitted order and per MEXC-only position
  not_reached.csv        FIREs / attempts that never became a MEXC position
  coverage.txt           counts + remaining gaps (also printed)

Nothing is guessed: a value that no source proves is left empty and the
reason is written in the "notes" column.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

OUT_DIR = BACKEND / "data" / "mexc_history"
WINDOW_MS = 7 * 86400 * 1000      # MEXC history queries are range-limited; walk in 7-day windows
PAGE = 100
REQ_PAUSE = 0.25                  # stay far under MEXC rate limits
TP_ATR, SL_ATR = 2.5, 1.5

# MEXC contract enums (documented values; unknown values are shown raw)
ORDER_STATE = {1: "UNINFORMED", 2: "UNCOMPLETED", 3: "COMPLETED", 4: "CANCELLED", 5: "INVALID"}
ORDER_SIDE = {1: "OPEN_LONG", 2: "CLOSE_SHORT", 3: "OPEN_SHORT", 4: "CLOSE_LONG"}
ORDER_CATEGORY = {1: "LIMIT", 2: "LIQUIDATION_TAKEOVER", 3: "CLOSE_DELEGATE", 4: "ADL_REDUCTION"}
STOP_STATE = {1: "UNTRIGGERED", 2: "CANCELLED", 3: "EXECUTED", 4: "INVALIDATED", 5: "EXEC_FAILED"}
STOP_TRIGGER_SIDE = {0: None, 1: "TP", 2: "SL"}

# Mirrors of production rules, used ONLY to label "consistent with" windows:
COOLDOWN_FAIL_REASONS = ("SL", "BRAIN_EXIT", "FLY", "cancel", "HARD_SL")   # autotrader_loop._in_cooldown
COOLDOWN_S = 3 * 300                                                       # cooldown_bars_after_failure=3 x 5m
LIVE_BLOCK_S = 15 * 60                                                     # autotrader_exec.REJECT_COOLDOWN_S
NO_BLOCK_REJECTS = ("MEXC already has an open position", "SL/TP no longer valid")  # rejects that do not call _block_live


# ------------------------------------------------------------------ helpers

def iso(ts):
    if ts is None:
        return ""
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sec(v):
    """MEXC times are ms; MiB times are s. Normalise to seconds (float)."""
    if v in (None, ""):
        return None
    v = float(v)
    return v / 1000.0 if v > 1e12 else v


def g(row, *names):
    for n in names:
        if row is not None and row.get(n) not in (None, ""):
            return row.get(n)
    return None


def f(row, *names):
    v = g(row, *names)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def rows_of(resp):
    data = resp.get("data") if isinstance(resp, dict) else resp
    if isinstance(data, dict):
        data = data.get("resultList") or data.get("result") or data.get("data") or []
    return data or []


def load_env():
    base = Path(os.environ.get("APPDATA", str(Path.home()))) / "mib-trader"
    for p in (base / ".env", BACKEND / ".env"):
        if not p.is_file():
            continue
        try:
            from dotenv import load_dotenv
            load_dotenv(p, override=False)
        except ImportError:
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ------------------------------------------------------------------ MEXC (GET only)

def _get(path, params):
    from src.market_data import mexc_private
    time.sleep(REQ_PAUSE)
    return mexc_private._request("GET", path, params=params)  # GET is the only verb this script uses


class Store:
    TABLES = {"positions": "positionId", "orders": "orderId", "deals": "id", "stoporders": "id"}

    def __init__(self, path):
        self.c = sqlite3.connect(path)
        for t in self.TABLES:
            self.c.execute(f"CREATE TABLE IF NOT EXISTS {t} (id TEXT PRIMARY KEY, json TEXT NOT NULL, fetched_at INTEGER)")
        self.c.execute("CREATE TABLE IF NOT EXISTS fetch_log (at INTEGER, endpoint TEXT, window TEXT, pages INTEGER, rows INTEGER, error TEXT)")
        self.c.commit()

    def put(self, table, rows):
        key = self.TABLES[table]
        now = int(time.time())
        n = 0
        for r in rows:
            rid = g(r, key, "id")
            if rid is None:
                rid = json.dumps(r, sort_keys=True)
            self.c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (str(rid), json.dumps(r), now))
            n += 1
        self.c.commit()
        return n

    def log(self, endpoint, window, pages, rows, error):
        self.c.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?,?)", (int(time.time()), endpoint, window, pages, rows, error))
        self.c.commit()

    def all(self, table):
        return [json.loads(r[0]) for r in self.c.execute(f"SELECT json FROM {table}")]


def paged(store, table, path, params, label):
    pages = total = 0
    err = None
    try:
        page = 1
        while True:
            rows = rows_of(_get(path, {**params, "page_num": page, "page_size": PAGE}))
            pages += 1
            total += store.put(table, rows)
            if len(rows) < PAGE:
                break
            page += 1
    except Exception as e:  # keep going; the gap is reported, never papered over
        err = f"{type(e).__name__}: {e}"
    store.log(path, label, pages, total, err)
    return total, err


def fetch_all(store, symbol, since_ms, until_ms):
    errors = []
    n, e = paged(store, "positions", "/position/list/history_positions", {"symbol": symbol}, "all pages")
    print(f"  closed positions : {n} rows" + (f"  ERROR {e}" if e else ""))
    if e:
        errors.append(("history_positions", e))
    for table, path in (("orders", "/order/list/history_orders"),
                        ("deals", "/order/list/order_deals"),
                        ("stoporders", "/stoporder/list/orders")):
        got = 0
        w = since_ms
        while w < until_ms:
            end = min(w + WINDOW_MS, until_ms)
            n, e = paged(store, table, path, {"symbol": symbol, "start_time": w, "end_time": end},
                         f"{iso(w / 1000)} -> {iso(end / 1000)}")
            got += n
            if e:
                errors.append((f"{path} {iso(w / 1000)}", e))
            w = end
        print(f"  {table:<16}: {got} rows")
    return errors


# ------------------------------------------------------------------ local MiB sources (read-only)

def load_sizing_log(path):
    out = []
    if not Path(path).is_file():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def load_paper_trades(db):
    if not Path(db).is_file():
        return []
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM paper_trades")]
    except sqlite3.OperationalError:
        rows = []
    c.close()
    for r in rows:
        try:
            r["thesis"] = json.loads(r.get("thesis_json") or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            r["thesis"] = {}
    return rows


# ------------------------------------------------------------------ ledger

def side_of_submitted(s):
    if not s:
        return None
    if f(s, "take_profit_price") is not None and f(s, "price") is not None:
        return "LONG" if s["take_profit_price"] > s["price"] else "SHORT"
    return None


def exit_group(mexc_reason, mib_label, pos, o_state):
    """TP / SL only when MEXC proves a stop-order trigger. A market close is
    split by MiB's own local label: MANUAL -> MANUAL, any other label ->
    LIFECYCLE (MiB closed it), no label -> MARKET_CLOSE_UNLABELLED."""
    if o_state in (4, 5):
        return "NOT_FILLED"
    if pos is None:
        return "OPEN_OR_MISSING"
    if mexc_reason in ("TP", "SL", "LIQUIDATION", "ADL"):
        return mexc_reason
    if (mexc_reason or "").startswith("MARKET_CLOSE"):
        if (mib_label or "").upper() == "MANUAL":
            return "MANUAL"
        return "LIFECYCLE" if mib_label else "MARKET_CLOSE_UNLABELLED"
    return "UNKNOWN"


def build(store, sizing, papers, replay_path):
    positions = {str(g(p, "positionId")): p for p in store.all("positions") if g(p, "positionId") is not None}
    orders = {str(g(o, "orderId", "id")): o for o in store.all("orders") if g(o, "orderId", "id") is not None}
    deals = store.all("deals")
    stops = store.all("stoporders")
    deals_by_order, deals_by_pos, stops_by_pos, orders_by_pos = {}, {}, {}, {}
    for d in deals:
        deals_by_order.setdefault(str(g(d, "orderId")), []).append(d)
        deals_by_pos.setdefault(str(g(d, "positionId")), []).append(d)
    for s in stops:
        stops_by_pos.setdefault(str(g(s, "positionId")), []).append(s)
    for o in orders.values():
        orders_by_pos.setdefault(str(g(o, "positionId")), []).append(o)
    paper_by_oid = {}
    for p in papers:
        oid = (p.get("thesis") or {}).get("order_id")
        if oid is not None:
            paper_by_oid[str(oid)] = p

    ledger, used_pos = [], set()
    submitted = [r for r in sizing if r.get("result") == "SUBMITTED"]
    for r in submitted:
        notes = []
        s = r.get("submitted") or {}
        oid = (r.get("order_result") or {}).get("data")
        oid = str(oid) if oid is not None else None
        side = side_of_submitted(s)
        o = orders.get(oid) if oid else None
        paper = paper_by_oid.get(oid) if oid else None
        th = (paper or {}).get("thesis") or {}
        if oid and o is None:
            notes.append("order id not found in MEXC order history")
        pid = str(g(o, "positionId")) if o and g(o, "positionId") is not None else None
        pos = positions.get(pid) if pid else None
        o_state = int(f(o, "state") or 0) if o else 0
        if o_state in (4, 5):
            notes.append(f"order {ORDER_STATE[o_state]}: never became a position")
        elif pid and pos is None:
            notes.append("position not in closed-position history (still open or outside history)")
        if pid:
            used_pos.add(pid)

        # fills of the opening order
        od = deals_by_order.get(oid, []) if oid else []
        fill_vol = sum(f(d, "vol") or 0 for d in od) or f(o, "dealVol")
        fill_px = (sum((f(d, "price") or 0) * (f(d, "vol") or 0) for d in od) / sum(f(d, "vol") or 0 for d in od)
                   if od and sum(f(d, "vol") or 0 for d in od) else f(o, "dealAvgPrice"))
        order_vol = f(o, "vol") or f(s, "vol")
        partial = None
        if fill_vol is not None and order_vol:
            partial = fill_vol + 1e-9 < order_vol
        if not od and o is not None:
            notes.append("no fill rows for opening order (order_deals); used order dealAvgPrice")

        # exit: fills of every other order on this position. Linked both by the
        # fill's own positionId (if MEXC returns it) and via the closing orders'
        # orderId (fills may not carry positionId).
        close_deals, seen = [], set()
        if pid:
            close_oids = {str(g(x, "orderId")) for x in orders_by_pos.get(pid, [])} - {oid}
            for d in deals_by_pos.get(pid, []) + [d for c in close_oids for d in deals_by_order.get(c, [])]:
                did = str(g(d, "id")) if g(d, "id") is not None else json.dumps(d, sort_keys=True)
                if str(g(d, "orderId")) != oid and did not in seen:
                    seen.add(did)
                    close_deals.append(d)
        exit_vol = sum(f(d, "vol") or 0 for d in close_deals)
        exit_px = (sum((f(d, "price") or 0) * (f(d, "vol") or 0) for d in close_deals) / exit_vol
                   if exit_vol else f(pos, "closeAvgPrice", "newCloseAvgPrice"))
        exit_ts = max((sec(g(d, "timestamp", "createTime")) or 0 for d in close_deals), default=None) or \
            (sec(g(pos, "updateTime")) if pos else None)

        # TP/SL: only a stop order that MEXC marks EXECUTED proves a TP/SL exit
        pst = stops_by_pos.get(pid, []) if pid else []
        executed = [x for x in pst if int(f(x, "state") or 0) == 3]
        exit_reason, reason_src = None, None
        trig_raw, trig_agrees = None, None
        if executed:
            # Which leg fired is decided by the ACTUAL exit price vs the planned
            # TP / SL we submitted -- not by trusting an enum meaning. MEXC's
            # triggerSide is kept raw and checked against it.
            trig_raw = g(executed[-1], "triggerSide")
            ptp, psl = f(s, "take_profit_price"), f(s, "stop_loss_price")
            if exit_px is not None and ptp is not None and psl is not None:
                by_price = "TP" if abs(exit_px - ptp) < abs(exit_px - psl) else "SL"
                enum_side = STOP_TRIGGER_SIDE.get(int(f(executed[-1], "triggerSide") or 0))
                trig_agrees = (enum_side == by_price) if enum_side else None
                exit_reason = by_price
                reason_src = (f"stop order EXECUTED; leg by exit price nearest planned TP/SL "
                              f"(MEXC triggerSide={trig_raw})")
            else:
                exit_reason, reason_src = "STOP_EXECUTED_LEG_UNKNOWN", "stop order EXECUTED; no exit price or planned levels"
        elif pid:
            close_orders = [x for x in orders_by_pos.get(pid, []) if str(g(x, "orderId")) != oid]
            cats = {ORDER_CATEGORY.get(int(f(x, "category") or 0), f"category {g(x, 'category')}") for x in close_orders}
            if "LIQUIDATION_TAKEOVER" in cats:
                exit_reason, reason_src = "LIQUIDATION", "closing order category"
            elif "ADL_REDUCTION" in cats:
                exit_reason, reason_src = "ADL", "closing order category"
            elif close_orders:
                exit_reason = "MARKET_CLOSE (MiB lifecycle or manual — not a TP/SL trigger)"
                reason_src = "closing order exists, no executed stop order"
            elif pos is not None:
                notes.append("closed position but no closing order/stop order found — exit reason unknown")
        if pid and not pst:
            notes.append("no TP/SL stop orders found for this position")

        # fees + pnl
        cs = f(r, "contract_size")
        open_notional = fill_vol * cs * fill_px if fill_vol and cs and fill_px else None
        close_notional = exit_vol * cs * exit_px if exit_vol and cs and exit_px else None
        takers = [g(d, "isTaker", "taker") for d in od + close_deals if g(d, "isTaker", "taker") is not None]
        fee_open = sum(abs(f(d, "fee") or 0) for d in od) if od else None
        fee_close = sum(abs(f(d, "fee") or 0) for d in close_deals) if close_deals else None
        fee_pos = f(pos, "fee", "totalFee")
        realised = f(pos, "realised")
        gross = f(pos, "closeProfitLoss")

        price, sl, tp = f(s, "price"), f(s, "stop_loss_price"), f(s, "take_profit_price")
        sl_d = abs(price - sl) if price is not None and sl is not None else None
        tp_d = abs(tp - price) if price is not None and tp is not None else None
        ratio = tp_d / sl_d if sl_d else None
        atr = None
        if ratio is not None and abs(ratio - TP_ATR / SL_ATR) < 0.02:
            atr = tp_d / TP_ATR
        elif ratio is not None:
            notes.append(f"TP/SL ratio {ratio:.2f} != 1.67: hunt entry != level, ATR not derivable")
        fire_bar = int(r["timestamp"]) - int(r["timestamp"]) % 300

        ledger.append({
            "kind": "MIB_SUBMITTED",
            "submit_time": iso(r["timestamp"]), "fire_5m_bar_utc": iso(fire_bar),
            "side": side, "mexc_order_id": oid, "external_oid": g(o, "externalOid"),
            "mexc_position_id": pid, "order_state": ORDER_STATE.get(int(f(o, "state") or 0)) if o else None,
            "order_error_code": g(o, "errorCode"),
            "paper_trade_id": (paper or {}).get("id"), "thesis_ts": th.get("thesis_ts"),
            "thesis_level": th.get("thesis_level"), "gate": th.get("gate"), "event": th.get("event"),
            "rearm": th.get("rearm"), "hunt_entry": th.get("hunt_entry"),
            "submitted_price": price, "planned_sl": sl, "planned_tp": tp,
            "planned_sl_dist": sl_d, "planned_tp_dist": tp_d, "atr_at_fire_derived": atr,
            "order_vol": order_vol, "filled_vol": fill_vol, "partial_fill": partial,
            "fill_count": len(od), "fill_price": fill_px,
            "entry_slippage": (fill_px - price) if fill_px is not None and price is not None else None,
            "actual_sl_dist_from_fill": abs(fill_px - sl) if fill_px is not None and sl is not None else None,
            "actual_tp_dist_from_fill": abs(tp - fill_px) if fill_px is not None and tp is not None else None,
            "position_open_time": iso(sec(g(pos, "createTime"))) if pos else "",
            "position_open_avg": f(pos, "openAvgPrice", "newOpenAvgPrice"),
            "exit_time": iso(exit_ts) if exit_ts else "", "exit_price": exit_px,
            "exit_reason": exit_reason, "exit_reason_source": reason_src,
            "stop_trigger_side_raw": trig_raw, "trigger_side_enum_agrees_with_price": trig_agrees,
            "stop_orders": ";".join(f"{STOP_STATE.get(int(f(x, 'state') or 0), g(x, 'state'))}/"
                                    f"{STOP_TRIGGER_SIDE.get(int(f(x, 'triggerSide') or 0)) or '-'}" for x in pst),
            "mib_exit_reason_local": (paper or {}).get("exit_reason"),  # MiB's own label, NOT MEXC-proven
            "mib_closed_at_local": iso(float(paper["closed_at"])) if paper and paper.get("closed_at") else "",
            "notional_usd": f(r, "final_notional"), "leverage": f(s, "leverage"),
            "realised_pnl": realised, "gross_pnl": gross,
            "fee_position": fee_pos, "fee_open_fills": fee_open, "fee_close_fills": fee_close,
            "contract_size": cs, "exit_vol": exit_vol or None,
            "open_notional": open_notional, "close_notional": close_notional,
            "fee_open_pct": 100 * fee_open / open_notional if fee_open is not None and open_notional else None,
            "fee_close_pct": 100 * fee_close / close_notional if fee_close is not None and close_notional else None,
            "taker_fills": sum(1 for t in takers if t), "maker_fills": sum(1 for t in takers if not t),
            "exit_group": exit_group(exit_reason, (paper or {}).get("exit_reason"), pos, o_state),
            "notes": " | ".join(notes),
        })

    # MEXC positions that MiB never logged submitting (manual, or pre-log)
    for pid, pos in positions.items():
        if pid in used_pos:
            continue
        ledger.append({
            "kind": "MEXC_ONLY", "mexc_position_id": pid,
            "side": {1: "LONG", 2: "SHORT"}.get(int(f(pos, "positionType") or 0)),
            "position_open_time": iso(sec(g(pos, "createTime"))),
            "position_open_avg": f(pos, "openAvgPrice", "newOpenAvgPrice"),
            "exit_time": iso(sec(g(pos, "updateTime"))), "exit_price": f(pos, "closeAvgPrice", "newCloseAvgPrice"),
            "realised_pnl": f(pos, "realised"), "gross_pnl": f(pos, "closeProfitLoss"),
            "fee_position": f(pos, "fee", "totalFee"), "exit_group": "MEXC_ONLY",
            "notes": "no SUBMITTED line in live_sizing_log for this position (manual trade, or before the log existed)",
        })

    # attempts that never became a MEXC position
    not_reached = []
    for r in sizing:
        if r.get("result") == "REJECTED":
            not_reached.append({"time": iso(r["timestamp"]), "fire_5m_bar_utc": iso(int(r["timestamp"]) // 300 * 300),
                                "side": None, "status": "REJECTED_BEFORE_SUBMIT", "proven_reason": r.get("rejection_reason"),
                                "source": "live_sizing_log", "notes": "side is not recorded on REJECTED lines"})
    for row in ledger:
        if row["kind"] == "MIB_SUBMITTED" and row.get("order_state") in ("CANCELLED", "INVALID"):
            not_reached.append({"time": row["submit_time"], "fire_5m_bar_utc": row["fire_5m_bar_utc"], "side": row["side"],
                                "status": f"MEXC_{row['order_state']}", "proven_reason": f"MEXC errorCode {row.get('order_error_code')}",
                                "source": "MEXC order history", "notes": ""})
    for p in papers:
        th = p.get("thesis") or {}
        if p.get("source") == "AUTO" and th.get("order_id") is None and p.get("opened_at"):
            not_reached.append({"time": iso(float(p["opened_at"])), "fire_5m_bar_utc": iso(int(float(p["opened_at"])) // 300 * 300),
                                "side": p.get("side"), "status": "PAPER_ONLY",
                                "proven_reason": "AUTO paper row with no MEXC order id (live not armed or pre-live)",
                                "source": "paper_trades", "notes": ""})

    replay_stats = None
    if replay_path:
        replay_stats = replay_compare(replay_path, sizing, ledger, positions, not_reached, papers)
    return ledger, not_reached, replay_stats


def consistent_windows(sizing, papers):
    """Time windows in which the live loop, by its own rules, would not send
    an order. Built only from MiB's own records. Used to label UNKNOWN FIREs
    as 'consistent with' -- never as the proven reason."""
    w = []
    for p in papers:
        if p.get("source") != "AUTO" or not p.get("opened_at"):
            continue
        a = float(p["opened_at"])
        b = float(p["closed_at"]) if p.get("closed_at") else float("inf")
        w.append(("SHADOW_POSITION_OPEN", a, b, f"paper {p.get('id')} open"))
        if p.get("closed_at") and (p.get("exit_reason") or "") in COOLDOWN_FAIL_REASONS:
            w.append(("COOLDOWN", b, b + COOLDOWN_S, f"after {p['exit_reason']} close {iso(b)}"))
    for r in sizing:
        why = r.get("rejection_reason") or ""
        if r.get("result") == "REJECTED" and not any(why.startswith(x) for x in NO_BLOCK_REJECTS):
            t = float(r["timestamp"])
            w.append(("LIVE_BLOCK_AFTER_REJECT", t, t + LIVE_BLOCK_S, f"reject {iso(t)}: {why[:50]}"))
    return w


def replay_compare(path, sizing, ledger, positions, not_reached, papers):
    """Replay FIREs inside the live period that have no MEXC order. The live
    loop never persists FIREs it drops, so the reason is only filled in when
    a record PROVES it; everything else is UNKNOWN."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    trades = data.get("trades") or []
    sub = [r for r in sizing if r.get("result") in ("SUBMITTED", "REJECTED")]
    if not sub:
        return {"error": "no live attempts in sizing log to define the live period"}
    t0, t1 = min(r["timestamp"] for r in sub), max(r["timestamp"] for r in sub)
    rts = [datetime.strptime(t["time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() for t in trades]
    if not rts:
        return {"error": "replay file has no trades"}
    r0, r1 = min(rts), max(rts)
    wins = consistent_windows(sizing, papers)
    submitted_bars = {}
    for row in ledger:
        if row["kind"] == "MIB_SUBMITTED":
            submitted_bars.setdefault(row["fire_5m_bar_utc"], []).append(row)
    rejected_bars = {}
    for r in sizing:
        if r.get("result") == "REJECTED":
            rejected_bars.setdefault(iso(int(r["timestamp"]) // 300 * 300), []).append(r)
    pos_iv = [(sec(g(p, "createTime")), sec(g(p, "updateTime")), pid) for pid, p in positions.items()
              if g(p, "createTime") and g(p, "updateTime")]
    stats = {"replay_file": str(path), "replay_covers": f"{iso(r0)} -> {iso(r1)}",
             "compare_window": f"{iso(max(t0, r0))} -> {iso(min(t1, r1))}",
             "replay_fires_in_live_period": 0, "matched_to_mexc_order": 0, "rejected_same_bar": 0,
             "position_open_proven": 0, "unknown": 0, "mexc_orders_without_replay_fire": 0}
    replay_keys = set()
    for t in trades:
        ts = datetime.strptime(t["time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
        if not (max(t0, r0) - 300 <= ts <= min(t1, r1) + 300):
            continue
        stats["replay_fires_in_live_period"] += 1
        bars = [iso(int(ts) // 300 * 300 + k * 300) for k in (-1, 0, 1)]
        replay_keys.update((b, t["side"]) for b in bars)
        if any(r["side"] == t["side"] for b in bars for r in submitted_bars.get(b, [])):
            stats["matched_to_mexc_order"] += 1
            continue
        rej = [r for b in bars for r in rejected_bars.get(b, [])]
        open_pos = [pid for a, b, pid in pos_iv if a is not None and b is not None and a <= ts <= b]
        if rej:
            status, why = "REPLAY_FIRE_REJECTED_SAME_BAR", rej[0].get("rejection_reason")
            stats["rejected_same_bar"] += 1
            note = "a REJECTED attempt exists in the same +/-1 5m bar; its side is not logged"
        elif open_pos:
            status, why = "REPLAY_FIRE_WHILE_POSITION_OPEN", f"MEXC position {open_pos[0]} was open"
            stats["position_open_proven"] += 1
            note = "live loop holds while a position is open"
        else:
            status, why = "REPLAY_FIRE_NOT_SENT", "UNKNOWN"
            stats["unknown"] += 1
            note = "no record proves why"
        cw = sorted({k for k, a, b, _ in wins if a <= ts <= b}) if status == "REPLAY_FIRE_NOT_SENT" else []
        not_reached.append({"time": t["time"], "fire_5m_bar_utc": bars[1], "side": t["side"], "status": status,
                            "proven_reason": why, "source": "replay", "notes": note,
                            "consistent_with": "+".join(cw) if status == "REPLAY_FIRE_NOT_SENT" else "",
                            "consistent_detail": "; ".join(d for k, a, b, d in wins if a <= ts <= b)[:200]
                            if status == "REPLAY_FIRE_NOT_SENT" else ""})
    stats["mexc_orders_outside_replay_window"] = 0
    stats["mexc_orders_matched_by_replay_fire"] = 0
    for row in ledger:
        if row["kind"] != "MIB_SUBMITTED":
            continue
        ts = datetime.strptime(row["submit_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        if not (r0 - 300 <= ts <= r1 + 300):
            stats["mexc_orders_outside_replay_window"] += 1
            row["replay_match"] = "OUTSIDE_REPLAY_WINDOW"
            continue
        if (row["fire_5m_bar_utc"], row["side"]) in replay_keys:
            stats["mexc_orders_matched_by_replay_fire"] += 1
            row["replay_match"] = "MATCHED"
            continue
        row["replay_match"] = "NO_REPLAY_FIRE"
        stats["mexc_orders_without_replay_fire"] += 1
        row["notes"] = (row["notes"] + " | " if row["notes"] else "") + "no replay FIRE within +/-1 5m bar, same side"
    return stats


# ------------------------------------------------------------------ report

def coverage(ledger, not_reached, fetch_errors, store, replay_stats):
    sub = [r for r in ledger if r["kind"] == "MIB_SUBMITTED"]
    only = [r for r in ledger if r["kind"] == "MEXC_ONLY"]
    L = []
    cnt = lambda pred: sum(1 for r in sub if pred(r))
    L.append("================ MEXC RAW (stored) ================")
    for t in Store.TABLES:
        L.append(f"  {t:<11}: {store.c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    L.append("\n================ LEDGER COVERAGE ================")
    L.append(f"MiB SUBMITTED orders (live_sizing_log): {len(sub)}")
    if sub:
        L.append(f"  period                 : {sub[0]['submit_time']} -> {sub[-1]['submit_time']}")
    rows = [
        ("found in MEXC order history", lambda r: r["order_state"] is not None),
        ("  COMPLETED", lambda r: r["order_state"] == "COMPLETED"),
        ("  CANCELLED / INVALID", lambda r: r["order_state"] in ("CANCELLED", "INVALID")),
        ("linked to a MEXC position id", lambda r: r["mexc_position_id"]),
        ("closed position found", lambda r: r["exit_price"] is not None),
        ("fill rows found", lambda r: r["fill_count"]),
        ("partial fill", lambda r: r["partial_fill"] is True),
        ("linked to paper_trades (thesis)", lambda r: r["paper_trade_id"]),
        ("ATR derivable (TP/SL = 1.67)", lambda r: r["atr_at_fire_derived"] is not None),
        ("exit reason TP (proven)", lambda r: r["exit_reason"] == "TP"),
        ("exit reason SL (proven)", lambda r: r["exit_reason"] == "SL"),
        ("exit by market close order", lambda r: (r["exit_reason"] or "").startswith("MARKET_CLOSE")),
        ("liquidation / ADL", lambda r: r["exit_reason"] in ("LIQUIDATION", "ADL")),
        ("exit reason unknown", lambda r: r["exit_price"] is not None and not r["exit_reason"]),
        ("realised P&L known", lambda r: r["realised_pnl"] is not None),
        ("fees known", lambda r: r["fee_position"] is not None or r["fee_open_fills"] is not None),
    ]
    for name, pred in rows:
        L.append(f"  {name:<32}: {cnt(pred)}")
    pnl = [r["realised_pnl"] for r in sub if r["realised_pnl"] is not None]
    fees = [r["fee_position"] for r in sub if r["fee_position"] is not None]
    if pnl:
        L.append(f"  sum realised P&L (USDT)          : {sum(pnl):.4f}  over {len(pnl)} positions")
    if fees:
        L.append(f"  sum position fees (USDT)         : {sum(abs(x) for x in fees):.4f}")
    slip = [r["entry_slippage"] for r in sub if r["entry_slippage"] is not None]
    if slip:
        s2 = sorted(slip)
        L.append(f"  entry slippage vs submitted price: median {s2[len(s2) // 2]:+.1f}  min {s2[0]:+.1f}  max {s2[-1]:+.1f}")
    L.append(f"MEXC positions with no MiB submit line: {len(only)}")
    L.append("\n================ NEVER REACHED A MEXC POSITION ================")
    by = {}
    for r in not_reached:
        by[r["status"]] = by.get(r["status"], 0) + 1
    for k, v in sorted(by.items()):
        L.append(f"  {k:<34}: {v}")
    if replay_stats:
        L.append("\n================ REPLAY vs REAL ================")
        for k, v in replay_stats.items():
            L.append(f"  {k:<34}: {v}")
    L.append("\n================ REMAINING GAPS ================")
    for ep, e in fetch_errors:
        L.append(f"  FETCH ERROR {ep}: {e}")
    gaps = {}
    for r in sub:
        for n in filter(None, (r["notes"] or "").split(" | ")):
            key = re.sub(r"-?\d+(\.\d+)?", "#", n)[:90]
            gaps[key] = gaps.get(key, 0) + 1
    for k, v in sorted(gaps.items(), key=lambda x: -x[1]):
        L.append(f"  {v:>4} x {k}")
    L.append("  Not recorded by MiB at all: FIREs dropped before submit (open position, weather, cooldown,")
    L.append("  same-5m, 15-min live block). Only --replay can list them; their reason stays UNKNOWN unless proven.")
    return "\n".join(L)


# ------------------------------------------------------------------ --summary

def _stats(v):
    v = [x for x in v if x is not None]
    if not v:
        return "n=0"
    s2 = sorted(v)
    med = s2[len(s2) // 2] if len(s2) % 2 else (s2[len(s2) // 2 - 1] + s2[len(s2) // 2]) / 2
    return f"n={len(v):<3} sum={sum(v):+9.4f}  avg={sum(v) / len(v):+8.4f}  median={med:+8.4f}"


def summary(ledger, not_reached, store=None):
    L = ["================ SUMMARY (USDT, from MEXC position history) ================"]
    if store is not None:
        L.append("MEXC field names seen (names only, no values):")
        for t in Store.TABLES:
            row = store.c.execute(f"SELECT json FROM {t} LIMIT 1").fetchone()
            L.append(f"  {t:<10}: {', '.join(sorted(json.loads(row[0]).keys())) if row else '-'}")
    agree = [r.get("trigger_side_enum_agrees_with_price") for r in ledger if r.get("stop_trigger_side_raw") is not None]
    if agree:
        L.append(f"TP/SL leg check: MEXC triggerSide enum agrees with exit price in {sum(1 for a in agree if a)}"
                 f" / {len(agree)} executed stop orders (disagree {sum(1 for a in agree if a is False)})")
    L.append("Exit groups: TP/SL = MEXC stop order EXECUTED; which leg = exit price nearest the planned TP or SL. LIFECYCLE / MANUAL = market close,")
    L.append("split by MiB's own local exit label (paper_trades.exit_reason).")
    closed = [r for r in ledger if r.get("realised_pnl") is not None]
    groups = {}
    for r in closed:
        groups.setdefault(r.get("exit_group") or "UNKNOWN", []).append(r)
    order = ["TP", "SL", "LIFECYCLE", "MANUAL", "MARKET_CLOSE_UNLABELLED", "LIQUIDATION", "ADL", "UNKNOWN", "MEXC_ONLY"]
    for gname in sorted(groups, key=lambda k: order.index(k) if k in order else 99):
        rows = groups[gname]
        L.append(f"\n[{gname}]  {len(rows)} positions")
        L.append(f"  gross (closeProfitLoss): {_stats([r.get('gross_pnl') for r in rows])}")
        L.append(f"  net   (realised)       : {_stats([r.get('realised_pnl') for r in rows])}")
        L.append(f"  fees  (position fee)   : {_stats([abs(r['fee_position']) if r.get('fee_position') is not None else None for r in rows])}")
        if gname == "LIFECYCLE":
            lab = {}
            for r in rows:
                lab[r.get("mib_exit_reason_local")] = lab.get(r.get("mib_exit_reason_local"), 0) + 1
            L.append(f"  MiB labels             : {lab}")
    sub = [r for r in ledger if r["kind"] == "MIB_SUBMITTED"]
    L.append("\n[ALL MiB-submitted, closed]")
    sc = [r for r in sub if r.get("realised_pnl") is not None]
    L.append(f"  gross: {_stats([r.get('gross_pnl') for r in sc])}")
    L.append(f"  net  : {_stats([r.get('realised_pnl') for r in sc])}")

    L.append("\n================ FEE RATE (% of notional = vol x contractSize x fill price) ================")
    fo = [r["fee_open_pct"] for r in sub if r.get("fee_open_pct") is not None]
    fc = [r["fee_close_pct"] for r in sub if r.get("fee_close_pct") is not None]
    L.append(f"  open  fills: {_stats(fo)}   (% per side)")
    L.append(f"  close fills: {_stats(fc)}   (% per side)")
    fills = sum((r.get("fee_open_fills") or 0) + (r.get("fee_close_fills") or 0) for r in sc)
    posfee = sum(abs(r.get("fee_position") or 0) for r in sc)
    L.append(f"  sum fill fees {fills:.4f} vs sum position fees {posfee:.4f}  (difference {posfee - fills:+.4f}: "
             "not explained by fills; may be funding or rounding -- not assumed)")
    rt = [(r.get("fee_open_pct") or 0) + (r.get("fee_close_pct") or 0) for r in sub
          if r.get("fee_open_pct") is not None and r.get("fee_close_pct") is not None]
    L.append(f"  round trip (open+close): {_stats(rt)}")
    tk = sum(r.get("taker_fills") or 0 for r in sub)
    mk = sum(r.get("maker_fills") or 0 for r in sub)
    L.append(f"  fills flagged taker: {tk}   maker: {mk}   (only where MEXC returned isTaker)")
    nb = [r for r in sub if r.get("open_notional")]
    if nb:
        L.append(f"  open notional per trade: {_stats([r['open_notional'] for r in nb])}")

    unk = [r for r in not_reached if r.get("status") == "REPLAY_FIRE_NOT_SENT"]
    if unk:
        L.append(f"\n================ {len(unk)} UNKNOWN REPLAY FIREs (never sent, reason NOT proven) ================")
        L.append("'consistent with' = the FIRE falls inside a window where MiB's own rules would hold. NOT proof.")
        cnt = {}
        for r in unk:
            cnt[r.get("consistent_with") or "NONE (still unknown)"] = cnt.get(r.get("consistent_with") or "NONE (still unknown)", 0) + 1
        for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
            L.append(f"  {v:>4}  consistent with {k}")
        L.append(f"\n  {'time (UTC)':<17} {'side':<6} consistent with")
        for r in unk:
            L.append(f"  {r['time']:<17} {r['side'] or '':<6} {r.get('consistent_with') or '-'}   {r.get('consistent_detail') or ''}")
    return "\n".join(L)


def write_csv(path, rows):
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(BACKEND / "market_data_clean.db"), help="read-only; for paper_trades")
    ap.add_argument("--sizing-log", default=str(BACKEND / "data" / "live_sizing_log.jsonl"))
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--since", help="YYYY-MM-DD (default: 2 days before the first sizing-log line)")
    ap.add_argument("--replay", help="tp_research_result.json from tp_research.py (optional)")
    ap.add_argument("--offline", action="store_true", help="do not call MEXC; rebuild ledger from stored rows")
    ap.add_argument("--summary", action="store_true", help="P&L by exit type, fee rate, unknown FIREs (read-only)")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitignore").write_text("*\n", encoding="utf-8")  # private data never gets committed
    store = Store(out / "mexc_history.db")
    sizing = load_sizing_log(a.sizing_log)
    papers = load_paper_trades(a.db)
    print(f"live_sizing_log: {len(sizing)} lines   paper_trades: {len(papers)} rows ({a.db})")

    fetch_errors = []
    if not a.offline:
        load_env()
        from src.market_data import mexc_private
        if not mexc_private.keys_present():
            sys.exit("MEXC_API_KEY / MEXC_API_SECRET not found (.env in %APPDATA%\\mib-trader or backend\\). "
                     "Use --offline to rebuild from stored data.")
        first = min((r["timestamp"] for r in sizing), default=time.time() - 30 * 86400)
        since = (datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc).timestamp() if a.since
                 else first - 2 * 86400)
        print(f"Fetching MEXC history (GET only) {iso(since)} -> now ...")
        fetch_errors = fetch_all(store, a.symbol, int(since * 1000), int(time.time() * 1000))
    else:
        for row in store.c.execute("SELECT endpoint, window, error FROM fetch_log WHERE error IS NOT NULL"):
            fetch_errors.append((f"{row[0]} {row[1]} (stored)", row[2]))

    ledger, not_reached, replay_stats = build(store, sizing, papers, a.replay)
    write_csv(out / "ledger.csv", ledger)
    (out / "ledger.json").write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    write_csv(out / "not_reached.csv", not_reached)
    rep = coverage(ledger, not_reached, fetch_errors, store, replay_stats)
    (out / "coverage.txt").write_text(rep, encoding="utf-8")
    print(rep)
    if a.summary:
        sm = summary(ledger, not_reached, store)
        (out / "summary.txt").write_text(sm, encoding="utf-8")
        print("\n" + sm)
    print(f"\nWritten to {out} (git-ignored)")


if __name__ == "__main__":
    main()
