"""Backfill BTC_USDT 1m futures candles from MEXC into the SAME database the backtest reads.

Walks BACKWARDS from the oldest 1m candle you already have, 2000 candles per request,
until it reaches --days back, or MEXC stops returning data (its history limit).
Safe to re-run: rows already present are skipped, and it resumes from the oldest row.

Run on your own machine (needs internet access to contract.mexc.com):
    python scripts/backfill_1m.py --days 365
    python scripts/backfill_1m.py --days 365 --forward      # also fill the gap up to "now"
    python scripts/backfill_1m.py --verify                  # only report gaps, no downloads
Close the trading app / server first so SQLite is not locked while this writes.
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SYMBOL = "BTC_USDT"
CHUNK = 1999 * 60          # stay under the 2000-candle-per-request limit


def make_fetcher(symbol=SYMBOL, retries=4):
    """Use the app's own MEXC connection method (Google DNS + IP-forced HTTPS with correct SNI),
    because the normal route to contract.mexc.com times out on this machine."""
    import http.client
    import json
    from src.market_data.mexc_market_data import (MEXC_HOST, MexcHTTPSConnection, resolve_mexc_ips)

    ips = resolve_mexc_ips()
    print(f"[mexc] resolved {MEXC_HOST} -> {ips}")

    def get(path):
        last = None
        for ip in ips:
            conn = None
            try:
                conn = MexcHTTPSConnection(ip=ip, hostname=MEXC_HOST, timeout=20)
                conn.request("GET", path, headers={"Host": MEXC_HOST, "User-Agent": "MIB-Trader/1.0",
                                                   "Accept": "application/json", "Connection": "close"})
                r = conn.getresponse()
                body = r.read()
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}: {body[:200]!r}")
                return json.loads(body.decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                last = e
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
        raise RuntimeError(f"could not reach MEXC via {ips}: {last}")

    def fetch(start, end):
        path = f"/api/v1/contract/kline/{symbol}?interval=Min1&start={int(start)}&end={int(end)}"
        for k in range(retries):
            try:
                js = get(path)
                if not js.get("success"):
                    raise RuntimeError(f"MEXC said: {js}")
                d = js.get("data") or {}
                t = d.get("time") or []
                return [(symbol, "1m", int(t[i]), float(d["open"][i]), float(d["high"][i]), float(d["low"][i]),
                         float(d["close"][i]), float(d["vol"][i])) for i in range(len(t))]
            except Exception:  # noqa: BLE001
                if k == retries - 1:
                    raise
                time.sleep(1.5 * (k + 1))
        return []
    return fetch


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS candles (
        symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL,
        high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
        PRIMARY KEY (symbol, timeframe, ts))""")


def bounds(con, symbol=SYMBOL):
    return con.execute("select min(ts), max(ts), count(*) from candles where symbol=? and timeframe='1m'", (symbol,)).fetchone()


def save(con, rows):
    con.executemany("INSERT OR IGNORE INTO candles (symbol,timeframe,ts,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()


def walk_back(con, fetch, target_start, sleep=0.25, empty_limit=3, log=print, start_from=None):
    """Download [target_start, oldest existing) going backwards. Returns rows written."""
    lo, _, _ = bounds(con)
    end = int(start_from if start_from is not None else (lo or time.time()))
    total, empties = 0, 0
    while end > target_start:
        start = max(int(target_start), end - CHUNK)
        rows = fetch(start, end)
        before = con.total_changes
        if rows:
            save(con, rows)
            empties = 0
        else:
            empties += 1
        wrote = con.total_changes - before
        total += wrote
        log(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(start))} -> {time.strftime('%Y-%m-%d %H:%M', time.gmtime(end))}: "
            f"{len(rows)} returned, {wrote} new (total {total})")
        if empties >= empty_limit:
            log("MEXC returned nothing for several windows in a row: this looks like its 1m history limit. Stopping.")
            break
        end = start
        time.sleep(sleep)
    return total


def walk_forward(con, fetch, sleep=0.25, log=print, now=None):
    _, hi, _ = bounds(con)
    if hi is None:
        return 0
    now = int(now if now is not None else time.time())
    start, total = hi, 0
    while start < now - 60:
        end = min(now, start + CHUNK)
        rows = fetch(start, end)
        before = con.total_changes
        if rows:
            save(con, rows)
        total += con.total_changes - before
        log(f"forward {time.strftime('%Y-%m-%d %H:%M', time.gmtime(start))}: {len(rows)} returned")
        start = end
        time.sleep(sleep)
    return total


def find_gaps(con, symbol=SYMBOL, min_gap=180):
    prev, gaps = None, []
    for (ts,) in con.execute("select ts from candles where symbol=? and timeframe='1m' order by ts", (symbol,)):
        if prev is not None and ts - prev > min_gap:
            gaps.append((prev, ts))
        prev = ts
    return gaps


def fill_gaps(con, fetch, sleep=0.25, log=print):
    total = 0
    for a, b in find_gaps(con):
        start = a
        while start < b:
            end = min(b, start + CHUNK)
            rows = fetch(start, end)
            before = con.total_changes
            if rows:
                save(con, rows)
            total += con.total_changes - before
            log(f"gap {time.strftime('%Y-%m-%d %H:%M', time.gmtime(start))}: {len(rows)} returned")
            start = end
            time.sleep(sleep)
    return total


def verify(con, log=print):
    lo, hi, n = bounds(con)
    if lo is None:
        log("no 1m candles")
        return
    expected = (hi - lo) // 60 + 1
    log(f"1m candles: {n}  from {time.strftime('%Y-%m-%d', time.gmtime(lo))} to {time.strftime('%Y-%m-%d', time.gmtime(hi))} "
        f"({(hi - lo) / 86400:.1f} days); expected {expected}; missing {expected - n} ({100 * (expected - n) / expected:.2f}%)")
    prev, gaps = None, []
    for (ts,) in con.execute("select ts from candles where symbol=? and timeframe='1m' order by ts", (SYMBOL,)):
        if prev is not None and ts - prev > 180:
            gaps.append((prev, ts))
        prev = ts
    big = sorted(gaps, key=lambda g: g[0] - g[1])[:10]
    log(f"gaps longer than 3 minutes: {len(gaps)}")
    for a, b in big:
        log(f"   {time.strftime('%Y-%m-%d %H:%M', time.gmtime(a))} -> {time.strftime('%Y-%m-%d %H:%M', time.gmtime(b))} ({(b - a) // 60} min)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="how far back from NOW to go")
    ap.add_argument("--forward", action="store_true", help="also fill from the newest row up to now")
    ap.add_argument("--fill-gaps", action="store_true", help="download the missing minutes inside the existing range")
    ap.add_argument("--verify", action="store_true", help="only report coverage and gaps")
    ap.add_argument("--db", default=None, help="database path (default: the one the backtest uses)")
    a = ap.parse_args()
    if a.db is None:
        from src.market_data import database as db
        a.db = db.db_path()
    print(f"[db] {a.db}")
    con = sqlite3.connect(a.db, timeout=60)
    ensure_table(con)
    if not a.verify:
        fetch = make_fetcher()
        target = int(time.time()) - a.days * 86400
        n = 0
        if a.fill_gaps:
            n += fill_gaps(con, fetch)
        else:
            n += walk_back(con, fetch, target)
        if a.forward:
            n += walk_forward(con, fetch)
        print(f"done: {n} new candles")
    verify(con)


if __name__ == "__main__":
    main()
