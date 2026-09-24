"""Proves normal_base actually resets to None when a trade closes,
using the real check_open_trades() SL/TP-close path against a real
isolated DB. Not just syntax-checked -- actually exercised.
"""
import os
import sys
import sqlite3
import tempfile
import shutil

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}  {detail}")


import sqlite3
tmp_dir = tempfile.mkdtemp()
tmp_db = os.path.join(tmp_dir, "market_data_clean.db")  # exact filename required to win DB-resolution tiering
_seed = sqlite3.connect(tmp_db)
_seed.execute("CREATE TABLE _pad (v TEXT)")
_seed.executemany("INSERT INTO _pad VALUES (?)", [("x" * 500,)] * 30)
_seed.commit()
_seed.close()
os.environ["MARKET_DB_PATH"] = tmp_db

from src import paper_trading as pt  # noqa: E402
from src.autotrader_state import STATE  # noqa: E402

pt.init_db()

# Simulate normal_base already having a (now-stale) captured value --
# exactly the situation this fix is meant to correct.
STATE["normal_base"] = 999.0
STATE["normal_base_captured_at"] = 123456.0

t = pt.open_trade("BTC_USDT", "LONG", 50000.0, 49700.0, 50500.0, 1000.0, source="AUTO")
check("setup: trade opened successfully", t is not None)
check("setup: normal_base still shows the stale value before any close", STATE["normal_base"] == 999.0)

# Price moves to hit TP -- this is the exact real path a live SL/TP
# fill goes through.
closed_count = pt.check_open_trades(price=50600.0)  # above TP
check("TP was actually detected and closed", closed_count == 1)
check("normal_base was reset to None after the close (forces fresh capture next sizing call)",
      STATE["normal_base"] is None)
check("normal_base_captured_at was also cleared", STATE["normal_base_captured_at"] is None)

# Second scenario: SL hit, same expectation
STATE["normal_base"] = 500.0
STATE["normal_base_captured_at"] = 111111.0
t2 = pt.open_trade("BTC_USDT", "SHORT", 50000.0, 50300.0, 49500.0, 1000.0, source="AUTO")
closed_count2 = pt.check_open_trades(price=50400.0)  # above SL for a SHORT
check("SL was actually detected and closed", closed_count2 == 1)
check("normal_base reset to None after an SL close too", STATE["normal_base"] is None)

# No open trades / no hit -- must NOT reset normal_base when nothing closed
STATE["normal_base"] = 777.0
closed_count3 = pt.check_open_trades(price=50000.0)  # no open trades left, nothing to hit
check("no close happened when there was nothing to hit", closed_count3 == 0)
check("normal_base is untouched when nothing actually closed", STATE["normal_base"] == 777.0)

shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)