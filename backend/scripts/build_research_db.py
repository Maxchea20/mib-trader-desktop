"""Build a SEPARATE research database with a long 1m history from Binance USD-M futures (BTCUSDT),
then create 5m/15m/30m/1h/4h/1d candles by AGGREGATING those 1m candles, so every timeframe comes
from one venue and is perfectly consistent.

Why: MEXC only serves about 7 weeks of 1m candles.  This is research data on a different venue than the
live MEXC feed (small price/wick differences), so treat results as a robustness test, not a replay of
your live fills.  Your MEXC database is never touched.

    python scripts/build_research_db.py --start 2025-09-01 --out research_binance.db
    python scripts/run_trend_break_backtest.py --setup-tf 15m --no-master --min-confidence 0.5 --db research_binance.db

Source: public bulk files at data.binance.vision (monthly zips + daily zips for the current month).
"""
import argparse
import csv
import datetime as dt
import io
import sqlite3
import time
import urllib.request
import zipfile

SYMBOL = "BTC_USDT"                       # label the rest of the code expects
BASE = "https://data.binance.vision/data/futures/um"
TFS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def parse_zip(blob):
    """Binance kline csv -> [(ts_sec, open, high, low, close, volume)]."""
    out = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            for row in csv.reader(io.TextIOWrapper(z.open(name), "utf-8")):
                if not row or not row[0].strip().lstrip("-").isdigit():
                    continue                                   # header line
                t = int(row[0])
                t = t // 1000 if t > 10**14 else t // 1000 if t > 10**11 else t   # ms (or us) -> s
                out.append((t, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
    return out


def aggregate(rows_1m, sec):
    """Complete buckets only (no partial candle at the edges)."""
    need = sec // 60
    b = {}
    for t, o, h, l, c, v in sorted(rows_1m):
        k = t - t % sec
        x = b.get(k)
        if x is None:
            b[k] = [k, o, h, l, c, v, 1]
        else:
            x[2], x[3], x[4], x[5], x[6] = max(x[2], h), min(x[3], l), c, x[5] + v, x[6] + 1
    return [tuple(x[:6]) for k, x in sorted(b.items()) if x[6] == need]


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS candles (
        symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
        low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, PRIMARY KEY (symbol, timeframe, ts))""")


def store(con, tf, rows):
    con.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", [(SYMBOL, tf) + tuple(r) for r in rows])
    con.commit()


def urls(start, end):
    """Monthly files for complete months, daily files for the rest."""
    first_of_this_month = end.replace(day=1)
    d = start.replace(day=1)
    while d < first_of_this_month:
        yield f"{BASE}/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{d:%Y-%m}.zip"
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    d = max(start, first_of_this_month)
    while d < end:                                   # up to yesterday
        yield f"{BASE}/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{d:%Y-%m-%d}.zip"
        d += dt.timedelta(days=1)


def download(url, retries=3):
    for k in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=90) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if k == retries - 1:
                print(f"   skipped ({e})")
                return None
            time.sleep(2 * (k + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=(dt.date.today() - dt.timedelta(days=380)).isoformat())
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--out", default="research_binance.db")
    a = ap.parse_args()
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    con = sqlite3.connect(a.out, timeout=60)
    ensure_table(con)
    all_1m = []
    for u in urls(start, end):
        print("downloading", u.rsplit("/", 1)[-1])
        blob = download(u)
        if blob:
            rows = parse_zip(blob)
            all_1m.extend(rows)
            print(f"   {len(rows)} candles")
    if not all_1m:
        print("nothing downloaded -- check the network (data.binance.vision)")
        return
    all_1m = sorted(set(all_1m))
    store(con, "1m", all_1m)
    for tf, sec in TFS.items():
        agg = aggregate(all_1m, sec)
        store(con, tf, agg)
        print(f"{tf}: {len(agg)} candles")
    lo, hi = all_1m[0][0], all_1m[-1][0]
    exp = (hi - lo) // 60 + 1
    print(f"1m: {len(all_1m)} candles, {dt.datetime.utcfromtimestamp(lo):%Y-%m-%d} -> {dt.datetime.utcfromtimestamp(hi):%Y-%m-%d} "
          f"({(hi - lo) / 86400:.0f} days), missing {exp - len(all_1m)} ({100 * (exp - len(all_1m)) / exp:.2f}%)")
    print(f"written to {a.out}")


if __name__ == "__main__":
    main()
