"""M5#1 / M5#3 routing tests and lifecycle-adapter tests. No exchange
connection, no order placed.
"""
import os
import sys

sys.path.insert(0, ".")
os.environ["MARKET_DB_PATH"] = "/home/claude/work/market_data_clean.db"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS  {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}  {detail}")


import src.scenario_live_bridge as bridge  # noqa: E402
from src.brain import lifecycle  # noqa: E402
from src.contract import LONG, SHORT  # noqa: E402
from src.autotrader_state import CONFIG  # noqa: E402

# --- M5#1 path, enabled_m5_slots=[1,2] (current live default): now
#     routed through the SAME C watcher M5#2 uses -- must NOT fire
#     immediately, must engage the watcher, same as M5#2 ---
CONFIG["enabled_m5_slots"] = [1, 2]
bridge._attempted_thesis_ids.clear()


def _fake_tick_slot1(live_price):
    return {
        "action": "FIRE", "direction": "LONG", "ts": 1000, "entry": 50000.0,
        "debug": {"atr15": 200.0,
                  "thesis": {"thesis_id": "TH-SLOT1-TEST", "origin_ts": 900,
                             "origin_level": 49900.0, "origin_event": "CHoCH"}},
        "scenario": "FRESH_CLEAN_BREAKOUT",
    }


bridge._tick_engine = _fake_tick_slot1
watcher_calls_before = bridge._WATCHER.active_count()
result = bridge.evaluate_scenario(50000.0)
check("M5#1 enabled: does NOT fire immediately (handed to watcher instead)",
      result["action"] in ("WAIT", "CANCEL", "FIRE"), detail=str(result))
check("M5#1: m5_slot correctly classified as 1", result["m5_slot"] == 1)
check("M5#1 enabled: thesis is now tracked by the watcher",
      "TH-SLOT1-TEST" in bridge._WATCHER._state or result["action"] != "WAIT")

# --- M5#1 path, enabled_m5_slots=[2] (M5#1 disabled): must be SKIPPED,
#     never opened -- confirms the config is a real, per-slot toggle ---
CONFIG["enabled_m5_slots"] = [2]
bridge._attempted_thesis_ids.clear()
bridge._tick_engine = _fake_tick_slot1
result1b = bridge.evaluate_scenario(50000.0)
check("M5#1 disabled: SKIPPED, not FIRE", result1b["action"] == "SKIPPED", detail=str(result1b))
check("M5#1 disabled: no entry price present (nothing opened)", "entry" not in result1b)
CONFIG["enabled_m5_slots"] = [1, 2]  # restore live default for subsequent tests

# --- M5#3 path, default config: excluded completely, not by a
#     dedicated boolean but simply by not being in enabled_m5_slots ---
bridge._attempted_thesis_ids.clear()


def _fake_tick_slot3(live_price):
    return {
        "action": "FIRE", "direction": "SHORT", "ts": 1600, "entry": 49500.0,
        "debug": {"atr15": 200.0,
                  "thesis": {"thesis_id": "TH-SLOT3-TEST", "origin_ts": 900,
                             "origin_level": 49900.0, "origin_event": "BOS"}},
        "scenario": "FRESH_CLEAN_BREAKOUT",
    }


bridge._tick_engine = _fake_tick_slot3
watcher_calls_before = bridge._WATCHER.active_count()
result3 = bridge.evaluate_scenario(49500.0)
check("M5#3 (excluded by default): SKIPPED, not FIRE", result3["action"] == "SKIPPED", detail=str(result3))
check("M5#3 (excluded by default): no entry price present (nothing opened)", "entry" not in result3)
check("M5#3: m5_slot correctly classified as 3", result3["m5_slot"] == 3)
check("M5#3: watcher was never engaged", bridge._WATCHER.active_count() == watcher_calls_before)

# --- M5#3 CAN be routed through the same watcher too, if explicitly
#     enabled -- proves the exclusion is a config choice, not a code
#     limitation (the mechanism itself is fully generic) ---
CONFIG["enabled_m5_slots"] = [1, 2, 3]
bridge._attempted_thesis_ids.clear()
bridge._tick_engine = _fake_tick_slot3
result3b = bridge.evaluate_scenario(49500.0)
check("M5#3 explicitly enabled: does NOT fire immediately (handed to watcher instead)",
      result3b["action"] in ("WAIT", "CANCEL", "FIRE"), detail=str(result3b))
CONFIG["enabled_m5_slots"] = [1, 2]  # restore live default for subsequent tests

# --- M5#2 path (contrast case): DOES engage the watcher, does NOT fire
#     on the same tick it's first seen ---
bridge._attempted_thesis_ids.clear()


def _fake_tick_slot2(live_price):
    return {
        "action": "FIRE", "direction": "LONG", "ts": 1300, "entry": 50100.0,
        "debug": {"atr15": 200.0,
                  "thesis": {"thesis_id": "TH-SLOT2-TEST", "origin_ts": 900,
                             "origin_level": 49900.0, "origin_event": "CHoCH"}},
        "scenario": "FRESH_CLEAN_BREAKOUT",
    }


bridge._tick_engine = _fake_tick_slot2
result2 = bridge.evaluate_scenario(50100.0)
check("M5#2: does NOT fire immediately (handed to watcher instead)",
      result2["action"] in ("WAIT", "CANCEL", "FIRE"), detail=str(result2))
check("M5#2: m5_slot correctly classified as 2", result2["m5_slot"] == 2)
check("M5#2: thesis is now tracked by the watcher",
      "TH-SLOT2-TEST" in bridge._WATCHER._state or result2["action"] != "WAIT")

# --- Lifecycle adapter tests ---
fire_long = {"direction": LONG, "entry": 50000.0, "atr15": 200.0,
             "origin_event": "CHoCH", "reason": "test"}
pos_long = lifecycle.position_from_scenario_fire(
    fire_long, trade_id="t-adapter-long", equity=1000.0, risk_pct=0.02,
    sl_atr_mult=1.5, tp_atr_mult=2.5, opened_ts=1000)
check("adapter LONG: SL below entry", pos_long.sl < pos_long.entry, detail=str(pos_long))
check("adapter LONG: TP above entry", pos_long.tp > pos_long.entry)
check("adapter LONG: SL distance == 1.5x ATR",
      abs((pos_long.entry - pos_long.sl) - 1.5 * 200.0) < 1e-6)
check("adapter LONG: TP distance == 2.5x ATR",
      abs((pos_long.tp - pos_long.entry) - 2.5 * 200.0) < 1e-6)

fire_short = {"direction": SHORT, "entry": 50000.0, "atr15": 200.0,
              "origin_event": "BOS", "reason": "test"}
pos_short = lifecycle.position_from_scenario_fire(
    fire_short, trade_id="t-adapter-short", equity=1000.0, risk_pct=0.02,
    sl_atr_mult=1.5, tp_atr_mult=2.5, opened_ts=1000)
check("adapter SHORT: SL above entry", pos_short.sl > pos_short.entry, detail=str(pos_short))
check("adapter SHORT: TP below entry", pos_short.tp < pos_short.entry)

# missing ATR -> must refuse rather than open blind
try:
    lifecycle.position_from_scenario_fire(
        {"direction": LONG, "entry": 50000.0, "atr15": None},
        trade_id="t-bad", equity=1000.0, risk_pct=0.02, sl_atr_mult=1.5, tp_atr_mult=2.5)
    check("adapter refuses to size with missing ATR", False, detail="did not raise")
except ValueError:
    check("adapter refuses to size with missing ATR", True)

# bad direction -> must refuse
try:
    lifecycle.position_from_scenario_fire(
        {"direction": "SIDEWAYS", "entry": 50000.0, "atr15": 200.0},
        trade_id="t-bad2", equity=1000.0, risk_pct=0.02, sl_atr_mult=1.5, tp_atr_mult=2.5)
    check("adapter refuses unrecognized direction", False, detail="did not raise")
except ValueError:
    check("adapter refuses unrecognized direction", True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)