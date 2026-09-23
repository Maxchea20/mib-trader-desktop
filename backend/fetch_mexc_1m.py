"""Backfill 1m BTC_USDT futures candles from MEXC into a SQLite db with
the same schema as this project's market_data.db.

NOT runnable in this sandbox -- contract.mexc.com isn't on the network
allowlist here. Copy this to your own machine (wherever the live MiB
Trader / autotrader normally runs, which already talks to MEXC) and run
it there.
"""
import sqlite3
import time
import requests

SYMBOL = "BTC_USDT"
INTERVAL = "Min1"
STEP_SECONDS = 2000 * 60  # 2000 candles * 60s
DB_PATH = r"C:\Users\User\Downloads\mib-trader-desktop-full\mib-trader-desktop\backend\market_data_clean.db"  # or wherever you want it written


def fetch_chunk(start_ts, end_ts):
    url = f"https://contract.mexc.com/api/v1/contract/kline/{SYMBOL}"
    r = requests.get(url, params={"interval": INTERVAL, "start": start_ts, "end": end_ts}, timeout=15)
    r.raise_for_status()
    data = r.json()["data"]
    rows = []
    for i in range(len(data["time"])):
        rows.append((SYMBOL, "1m", data["time"][i], data["open"][i],
                      data["high"][i], data["low"][i], data["close"][i], data["vol"][i]))
    return rows


def backfill(start_ts, end_ts):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS candles (
        symbol TEXT, timeframe TEXT, ts INTEGER, open REAL, high REAL,
        low REAL, close REAL, volume REAL,
        PRIMARY KEY (symbol, timeframe, ts))""")

    t = start_ts
    total = 0
    while t < end_ts:
        chunk_end = min(t + STEP_SECONDS, end_ts)
        rows = fetch_chunk(t, chunk_end)
        cur.executemany(
            "INSERT OR IGNORE INTO candles (symbol,timeframe,ts,open,high,low,close,volume) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
        total += len(rows)
        print(f"{t} -> {chunk_end}: {len(rows)} candles (total {total})")
        t = chunk_end
        time.sleep(0.15)  # stay well under 20 req/2s

    con.close()
    print(f"Done. {total} candles written.")


if __name__ == "__main__":
    # example: last 365 days
    now = int(time.time())
backfill(now - 3600, now)