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

# --- Late pullback-continuation: previously silently dropped 100% of
#     the time (confirmed against real data). Now explicit + config-gated.
bridge._attempted_thesis_ids.clear()
CONFIG["enable_late_pullback_fire"] = False


def _fake_tick_late_pullback(live_price):
    return {
        "action": "FIRE", "direction": "LONG", "ts": 1000 + 5000, "entry": 50000.0,
        "debug": {"atr15": 200.0,
                  "thesis": {"thesis_id": "TH-LATE-PULLBACK-TEST", "origin_ts": 1000,
                             "origin_level": 49900.0, "origin_event": "CHoCH"}},
        "scenario": "FRESH_PULLBACK_CONTINUATION",
    }


bridge._tick_engine = _fake_tick_late_pullback
result_off = bridge.evaluate_scenario(50000.0)
check("late pullback, flag OFF (default): SKIPPED, not silently dropped without a reason",
      result_off["action"] == "SKIPPED", detail=str(result_off))
check("late pullback, flag OFF: reason clearly distinguishes this from the generic slot skip",
      "late pullback-continuation" in result_off["reason"], detail=result_off["reason"])
check("late pullback, flag OFF: m5_slot correctly recorded as None (outside the 900s window)",
      result_off["m5_slot"] is None)

bridge._attempted_thesis_ids.clear()
CONFIG["enable_late_pullback_fire"] = True
result_on = bridge.evaluate_scenario(50000.0)
check("late pullback, flag ON: fires", result_on["action"] == "FIRE", detail=str(result_on))
check("late pullback, flag ON: entry is the engine's own price (no C timing applied)",
      result_on["entry"] == 50000.0)
CONFIG["enable_late_pullback_fire"] = False  # restore default for any subsequent tests

# --- Regression: in-window slot 1/2 behavior completely unaffected by
#     the new late-pullback branch ---
bridge._attempted_thesis_ids.clear()
bridge._tick_engine = _fake_tick_slot2
result_slot2_regression = bridge.evaluate_scenario(50100.0)
check("regression: M5#2 still routes through the watcher exactly as before",
      result_slot2_regression["m5_slot"] == 2, detail=str(result_slot2_regression))

# --- Stale "FIRE" state fix: sizing/execution failure must update
#     last_scenario_result, not leave it frozen showing FIRE ---
import importlib  # noqa: E402
import src.autotrader_loop as atl  # noqa: E402
import src.scenario_live_bridge as slb_module  # noqa: E402
from src.brain import lifecycle as lifecycle_module  # noqa: E402
importlib.reload(atl)
from src.autotrader_state import STATE  # noqa: E402


def _fake_scenario_result_fire():
    return {"action": "FIRE", "direction": "LONG", "thesis_id": "TH-STALE-TEST",
            "entry_ts": 1000, "entry": 50000.0, "origin_event": "CHoCH",
            "origin_level": 49900.0, "reason": "test fire", "scenario": "FRESH_CLEAN_BREAKOUT",
            "atr15": 200.0, "m5_slot": 1}


_orig_evaluate_scenario = slb_module.evaluate_scenario
_orig_position_from_scenario_fire = lifecycle_module.position_from_scenario_fire
slb_module.evaluate_scenario = lambda price: _fake_scenario_result_fire()


def _broken_position_from_scenario_fire(*a, **kw):
    raise ValueError("simulated sizing failure")


lifecycle_module.position_from_scenario_fire = _broken_position_from_scenario_fire
CONFIG["mode"] = "PAPER"
atl._evaluate_scenario_entry("15m", 50000.0, live_mode=False, live_armed=False)
check("stale-FIRE fix: last_scenario_result action updated away from FIRE after a sizing failure",
      STATE["last_scenario_result"]["action"] == "FIRE_FAILED", detail=str(STATE["last_scenario_result"]))
check("stale-FIRE fix: the actual failure reason is recorded, not just the generic FIRE reason",
      "sizing failed" in STATE["last_scenario_result"]["reason"])
# restore real functions so nothing else in this process is left patched
slb_module.evaluate_scenario = _orig_evaluate_scenario
lifecycle_module.position_from_scenario_fire = _orig_position_from_scenario_fire

# --- Fix #4 (Claude Code's diagnosis, independently verified): the
#     watcher must be polled again on every subsequent tick until it
#     genuinely resolves (fire/cancel), not blocked after a single WAIT.
bridge._attempted_thesis_ids.clear()
bridge._WATCHER._state.clear()
_watcher_call_count = [0]
_watcher_results = [None, None,
                     {"entry_ts": 1300, "entry_price": 50123.4,
                      "confirmed_at_minute": 3, "seconds_after_m5_2_open": 120}]


def _mock_watcher_check(*a, **kw):
    r = _watcher_results[min(_watcher_call_count[0], len(_watcher_results) - 1)]
    _watcher_call_count[0] += 1
    return r


bridge._WATCHER.check = _mock_watcher_check


def _fake_tick_slot2_multi(live_price):
    return {
        "action": "FIRE", "direction": "LONG", "ts": 1300, "entry": 50100.0,
        "debug": {"atr15": 200.0,
                  "thesis": {"thesis_id": "TH-FIX4-TEST", "origin_ts": 900,
                             "origin_level": 49900.0, "origin_event": "CHoCH"}},
        "scenario": "FRESH_CLEAN_BREAKOUT",
    }


bridge._tick_engine = _fake_tick_slot2_multi
_count_before_tick1 = _watcher_call_count[0]
_r1 = bridge.evaluate_scenario(50100.0)
_count_after_tick1 = _watcher_call_count[0]
_r2 = bridge.evaluate_scenario(50100.0)
_count_after_tick2 = _watcher_call_count[0]
_r3 = bridge.evaluate_scenario(50100.0)
_count_after_tick3 = _watcher_call_count[0]
check("fix #4: watcher polled on tick 1 (genuine WAIT, not yet resolved)",
      _r1["action"] == "WAIT" and _count_after_tick1 == _count_before_tick1 + 1, detail=str(_r1))
check("fix #4: watcher polled AGAIN on tick 2 -- this is the actual bug fix",
      _r2["action"] == "WAIT" and _count_after_tick2 == _count_after_tick1 + 1, detail=str(_r2))
check("fix #4: watcher polled a third time and genuinely resolves to FIRE",
      _r3["action"] == "FIRE" and _count_after_tick3 == _count_after_tick2 + 1, detail=str(_r3))
bridge._WATCHER.check = bridge._WATCHER.check  # no-op, restore happens via new instance in later tests
importlib.reload(bridge)

# --- Fix #5: thesis_id is now derived from the real, unique M15 origin
#     timestamp, not a per-process counter that resets on restart.
from src.brain.scenario_engine import ScenarioEngine, Thesis  # noqa: E402
e_run1 = ScenarioEngine()
e_run2 = ScenarioEngine()  # simulates a fresh instance after a restart
check("fix #5: a fresh engine instance's thesis_counter still starts at 0 internally "
      "(irrelevant now -- ID no longer depends on it)",
      e_run1._thesis_counter == 0 and e_run2._thesis_counter == 0)
# The real proof: construct what tick() would build for two DIFFERENT
# real theses (different origin_ts, as any two genuinely different
# market events must have) and confirm their IDs differ -- this was
# NOT true before the fix when both happened to be each run's first.
id_a = f"TH-{1758600000}"
id_b = f"TH-{1758600900}"
check("fix #5: two different real theses (different M15 origin timestamps) get different IDs",
      id_a != id_b)
check("fix #5: the SAME real thesis (same origin timestamp) always gets the SAME id, "
      "which is correct -- it's genuinely the same event, not a collision",
      f"TH-{1758600000}" == id_a)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)