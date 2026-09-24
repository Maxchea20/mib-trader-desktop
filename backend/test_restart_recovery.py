"""Restart-recovery tests for the scenario_c_watch persistence layer.
Uses a throwaway SQLite file, never touches the real market data DB's
candle tables. No exchange connection, no order placed.
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}  {detail}")


# isolated throwaway DB file for this test only. resolve_db_path()
# (pre-existing, unrelated to this change) ranks any candidate literally
# named "market_data_clean.db" ahead of all others regardless of the env
# var, then falls back to file size -- so isolation requires BOTH that
# exact filename AND a size over its 10KB floor.
import sqlite3
tmp_dir = tempfile.mkdtemp()
tmp_db = os.path.join(tmp_dir, "market_data_clean.db")
_seed = sqlite3.connect(tmp_db)
_seed.execute("CREATE TABLE _pad (v TEXT)")
_seed.executemany("INSERT INTO _pad VALUES (?)", [("x" * 500,)] * 30)  # push past the 10KB floor
_seed.commit()
_seed.close()
os.environ["MARKET_DB_PATH"] = tmp_db

from src.market_data import database as db  # noqa: E402

db.init_db()
assert db._db_path() == tmp_db, f"DB isolation failed: using {db._db_path()} instead of {tmp_db}"

# --- Test: save + reload a 'watching' row ---
db.save_scenario_watch("TH-RESTART-1", direction="LONG", origin_ts=1000, origin_level=50000.0,
                        m5_slot=2, status="watching", checked_ts='[1300, 1360]', started_at=1300.0)
rows = db.load_all_scenario_watch()
check("save_scenario_watch persists a row", len(rows) == 1, detail=str(rows))
check("persisted row has correct status", rows[0]["status"] == "watching")
check("persisted row round-trips checked_ts", rows[0]["checked_ts"] == '[1300, 1360]')

# --- Test: EntryTimingCWatcher resumes a 'watching' thesis from DB on fresh init ---
from src.brain.entry_timing_c_watcher import EntryTimingCWatcher  # noqa: E402

w = EntryTimingCWatcher()
check("watcher restores in-flight watch from DB on construction",
      "TH-RESTART-1" in w._state, detail=str(w._state.keys()))
check("watcher restores previously-checked M1 timestamps",
      w._state.get("TH-RESTART-1", {}).get("checked_ts") == {1300, 1360})

# --- Test: a 'fired' row is NOT resumed as an active watch (it's done) ---
db.save_scenario_watch("TH-RESTART-2", direction="SHORT", origin_ts=2000, origin_level=51000.0,
                        m5_slot=2, status="fired", entry_ts=2360, entry_price=51005.0)
w2 = EntryTimingCWatcher()
check("a completed ('fired') thesis is not resumed as an active watch",
      "TH-RESTART-2" not in w2._state)

# --- Test: a 'cancelled' row is NOT resumed either ---
db.save_scenario_watch("TH-RESTART-3", direction="LONG", origin_ts=3000, origin_level=52000.0,
                        m5_slot=2, status="cancelled", reason="sync gap")
w3 = EntryTimingCWatcher()
check("a cancelled thesis is not resumed as an active watch",
      "TH-RESTART-3" not in w3._state)

# --- Test: scenario_live_bridge's duplicate-attempt set is restored from
#     ALL statuses (watching + fired + cancelled), simulating a fresh
#     process import after "restart" ---
import importlib  # noqa: E402
import src.scenario_live_bridge as bridge  # noqa: E402
importlib.reload(bridge)  # re-run module-level _restore_attempted_ids()

check("duplicate-attempt guard restored from DB includes 'watching' thesis",
      "TH-RESTART-1" in bridge._attempted_thesis_ids)
check("duplicate-attempt guard restored from DB includes 'fired' thesis",
      "TH-RESTART-2" in bridge._attempted_thesis_ids)
check("duplicate-attempt guard restored from DB includes 'cancelled' thesis",
      "TH-RESTART-3" in bridge._attempted_thesis_ids)

# --- Test: a genuinely never-seen thesis is NOT in the restored set ---
check("a never-seen thesis is correctly absent from the restored guard",
      "TH-NEVER-SEEN" not in bridge._attempted_thesis_ids)

# --- Test: updating an existing row (e.g. watching -> fired) preserves
#     fields not explicitly passed (COALESCE-style merge) ---
db.save_scenario_watch("TH-RESTART-1", status="fired", entry_ts=1420, entry_price=50010.0,
                        reason="confirmed at minute 2")
updated = [r for r in db.load_all_scenario_watch() if r["thesis_id"] == "TH-RESTART-1"][0]
check("update preserves direction not re-passed", updated["direction"] == "LONG",
      detail=str(updated))
check("update preserves origin_level not re-passed", updated["origin_level"] == 50000.0)
check("update applies the new status", updated["status"] == "fired")
check("update applies the new entry_price", updated["entry_price"] == 50010.0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)

import shutil
shutil.rmtree(tmp_dir, ignore_errors=True)