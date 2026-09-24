"""Cross-references every thesis marked 'fired' in scenario_c_watch
against actual trade records in paper_trades. Flags any 'fired' thesis
with no matching trade -- i.e. the system decided to fire but no order
(paper or live) was ever actually recorded. Read-only, changes nothing.

Run this from your backend/ folder with your REAL market_data_clean.db
in place (do not move it aside for this one).
"""
import json
import sqlite3
import sys

sys.path.insert(0, ".")
from src.market_data import database as db  # noqa: E402
from src import paper_trading as pt  # noqa: E402

path = db._db_path()
print(f"Reading from: {path}\n")

con = sqlite3.connect(path)
con.row_factory = sqlite3.Row

# Every thesis the system ever recorded as actually firing.
try:
    fired = con.execute("SELECT * FROM scenario_c_watch WHERE status='fired' ORDER BY updated_at").fetchall()
except sqlite3.OperationalError:
    print("No scenario_c_watch table found -- nothing has gone through the M5#1/M5#2 watcher yet, "
          "or you're pointed at the wrong database file.")
    fired = []

print(f"Theses marked 'fired' in scenario_c_watch: {len(fired)}\n")

# Every trade the system actually opened, with its thesis_id (if any)
# pulled out of the stored thesis JSON.
all_trades = pt.list_trades()  # no status filter -- OPEN and CLOSED both
trade_thesis_ids = set()
for t in all_trades:
    tj = t.get("thesis") or {}
    tid = tj.get("thesis_id")
    if tid:
        trade_thesis_ids.add(tid)

print(f"Total trade records in paper_trades: {len(all_trades)}")
print(f"Of those, trades that carry a recognizable thesis_id: {len(trade_thesis_ids)}\n")

orphans = []
for row in fired:
    tid = row["thesis_id"]
    if tid not in trade_thesis_ids:
        orphans.append(dict(row))

print("=" * 70)
if not orphans:
    print("RESULT: every 'fired' thesis has a matching trade record.")
    print("No thesis-says-fire-but-no-trade cases found in this database.")
else:
    print(f"RESULT: found {len(orphans)} thesis(es) marked 'fired' with NO matching trade record.")
    print("These are the real 'fire but nothing happened' cases:\n")
    for o in orphans:
        print(f"  thesis_id       : {o['thesis_id']}")
        print(f"  direction       : {o.get('direction')}")
        print(f"  m5_slot         : {o.get('m5_slot')}")
        print(f"  origin_ts       : {o.get('origin_ts')}")
        print(f"  entry_ts        : {o.get('entry_ts')}")
        print(f"  entry_price     : {o.get('entry_price')}")
        print(f"  reason          : {o.get('reason')}")
        print(f"  updated_at      : {o.get('updated_at')}")
        print()
print("=" * 70)

# Secondary check: any scenario_c_watch row of ANY status whose reason
# text mentions a failure, for extra visibility beyond just 'fired'.
try:
    failures = con.execute(
        "SELECT * FROM scenario_c_watch WHERE reason LIKE '%fail%' OR reason LIKE '%FAILED%' "
        "ORDER BY updated_at"
    ).fetchall()
    if failures:
        print(f"\nAdditionally, {len(failures)} scenario_c_watch row(s) mention a failure in their reason text:")
        for f in failures:
            print(f"  {f['thesis_id']}  status={f['status']}  reason={f['reason']}")
except sqlite3.OperationalError:
    pass

con.close()