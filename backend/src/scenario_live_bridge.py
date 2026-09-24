"""Scenario live bridge. NEW module.

Wires scenario_engine.py (UNCHANGED) and entry_timing_c.py (UNCHANGED)
into the existing live evaluation loop, lifecycle, and execution
machinery. Does not touch scenario_engine.py's internals, does not
touch entry_timing_c.py's internals, does not duplicate lifecycle.py's
SL/TP/position logic (uses lifecycle.position_from_scenario_fire, the
thin adapter added alongside this), and does not duplicate
autotrader_exec.py's order mechanics.

Selected as the live entry engine only when
autotrader_state.CONFIG["entry_engine"] == "scenario" (default remains
"legacy" -- see autotrader_loop.py). When selected, this module is the
ONLY thing permitted to open a NEW scenario-originated trade; once open,
management reverts entirely to the existing, unchanged
brain/lifecycle_tick.py machinery, same as any legacy-originated trade.

One entry attempt per thesis (duplicate-entry guard) is enforced here
via _attempted_thesis_ids, a persistent, growing set for the life of the
process. This is intentionally simple over being "clever": it trades a
small amount of memory for an unambiguous guarantee.
"""
import time
from typing import Dict, Optional

from .config import ANALYSIS_LOOKBACK
from .market_data import data_access as dao
from .market_data import database as db
from .brain.scenario_engine import ScenarioEngine
from .brain.entry_timing_c_watcher import EntryTimingCWatcher
from .autotrader_state import CONFIG, logger

_ENGINE = ScenarioEngine()
_WATCHER = EntryTimingCWatcher()


def _restore_attempted_ids() -> set:
    """Restart safety for the duplicate-attempt guard: any thesis_id ever
    recorded in scenario_c_watch (watching, fired, OR cancelled) has
    already been handled and must never be attempted again, across a
    restart. Uses the same persistent store as the watcher -- no second
    persistence system. Falls back to an empty set (fail-open on the
    guard, not on real order safety -- the existing DB-backed open-
    position check in autotrader_loop.py::evaluate() remains the actual
    backstop against a duplicate live order regardless)."""
    try:
        return {row["thesis_id"] for row in db.load_all_scenario_watch()}
    except Exception:
        return set()


_attempted_thesis_ids = _restore_attempted_ids()


def classify_m5_slot(origin_ts: Optional[int], event_ts: Optional[int]) -> Optional[int]:
    """Which of the 3 five-minute candles (1/2/3) since the thesis's M15
    origin does event_ts fall on? Pure timestamp arithmetic -- the exact
    same convention used throughout this session's Case-1 research
    (cross_slot detection in the backtest scripts). Both timestamps are
    5-minute-aligned by construction (15m and 5m candle boundaries)."""
    if origin_ts is None or event_ts is None:
        return None
    delta = event_ts - origin_ts
    if delta < 0 or delta >= 900:
        return None
    return int(delta // 300) + 1


def _tick_engine(live_price: Optional[float]) -> Dict:
    """Runs one scenario_engine.tick() off real stored candles. Builds a
    forming-5m proxy from live tick price ONLY (never fabricates a 1m
    candle for this purpose -- that restriction belongs to the M1
    watcher and is unrelated to this coarser M5 tick)."""
    candles_15m = dao.read_closed_candles("15m", limit=ANALYSIS_LOOKBACK)
    candles_5m = dao.read_closed_candles("5m", limit=ANALYSIS_LOOKBACK)
    if len(candles_15m) < 60 or len(candles_5m) < 60:
        return {"action": "WAIT", "reason": "insufficient candle history", "debug": {"thesis": None}}

    forming_5m = None
    if candles_5m and live_price:
        forming_ts = candles_5m[-1]["ts"] + 300
        if forming_ts <= int(time.time()):
            forming_5m = {"ts": forming_ts, "open": live_price, "high": live_price,
                           "low": live_price, "close": live_price, "volume": 0.0}

    return _ENGINE.tick(candles_15m[-200:], candles_5m[-200:], forming_5m)


def evaluate_scenario(live_price: Optional[float]) -> Dict:
    """Sibling to autotrader_loop.py::evaluate()'s entry-decision role,
    for the scenario engine. Returns a dict describing what happened;
    the caller (autotrader_loop.py) is responsible for actually invoking
    execution (lifecycle.position_from_scenario_fire + autotrader_exec)
    on a "FIRE" result -- this function decides, it does not place orders,
    keeping the existing execution path as the single place real orders
    are ever created.
    """
    out = _tick_engine(live_price)

    if out["action"] != "FIRE":
        return {"action": "WAIT", "reason": out.get("what_happening"), "scenario": out.get("scenario")}

    thesis_dbg = out.get("debug", {}).get("thesis")
    if thesis_dbg is None:
        return {"action": "WAIT", "reason": "engine reported FIRE with no thesis debug -- refusing, should not happen"}

    thesis_id = thesis_dbg["thesis_id"]
    origin_ts = thesis_dbg["origin_ts"]
    event_ts = out["ts"]
    slot = classify_m5_slot(origin_ts, event_ts)

    log_base = {
        "thesis_id": thesis_id, "direction": out["direction"],
        "origin_event": thesis_dbg["origin_event"], "origin_level": thesis_dbg["origin_level"],
        "origin_ts": origin_ts, "m5_confirmation_ts": event_ts, "m5_slot": slot,
        "scenario": out["scenario"], "atr15": out["debug"]["atr15"],
    }

    if thesis_id in _attempted_thesis_ids:
        return {"action": "ALREADY_ATTEMPTED", "reason": f"thesis {thesis_id} already attempted", **log_base}

    enabled_slots = CONFIG.get("enabled_m5_slots", [1, 2])

    if slot is not None and slot in enabled_slots:
        # Same C mechanism, same watcher, same find_m5_2_intrabar_entry()
        # rule for every enabled slot -- only the window watched differs
        # (that slot's own 5-minute window). No per-slot tuning, and no
        # hardcoded slot restriction here: which slots are eligible is
        # controlled entirely by CONFIG["enabled_m5_slots"], so enabling
        # slot 3 there actually routes it through this same path too.
        #
        # BUG FIX: thesis_id used to be added to _attempted_thesis_ids
        # HERE, before the watcher's answer was even known. Since the
        # top-of-function check above treats any id in that set as
        # already-handled, a genuine "still waiting, ask again" result
        # was being marked exactly the same as a real fire or cancel --
        # meaning the watcher was only ever actually polled ONCE per
        # thesis, no matter how many ticks followed. Confirmed directly:
        # calling this function three times in a row for the same
        # pending thesis only ever invoked the watcher once. Now only
        # marked attempted once the outcome is actually terminal.
        c_result = _WATCHER.check(thesis_id, out["direction"], thesis_dbg["origin_level"], origin_ts, slot)
        if c_result is None:
            logger.info(f"[scenario] M5#{slot} thesis {thesis_id}: watching M1 for confirming close.")
            return {"action": "WAIT", "reason": f"M5#{slot}: watching M1 for confirming close", **log_base}
        if c_result.get("cancelled"):
            _attempted_thesis_ids.add(thesis_id)
            logger.warning(f"[scenario] M5#{slot} thesis {thesis_id}: CANCELLED -- {c_result['reason']}")
            return {"action": "CANCEL", "reason": c_result["reason"], **log_base}
        _attempted_thesis_ids.add(thesis_id)
        entry_price = c_result["entry_price"]
        entry_ts = c_result["entry_ts"]
        reason = (f"C: M1 close confirmed at minute {c_result['confirmed_at_minute']} of M5#{slot} "
                  f"({c_result['seconds_after_m5_2_open']}s after M5#{slot} open)")
        return {
            "action": "FIRE", "direction": out["direction"], "entry": entry_price,
            "entry_ts": entry_ts, "c_intended_price": entry_price, "c_intended_ts": entry_ts,
            "atr15": out["debug"]["atr15"], "reason": reason, **log_base,
        }

    # Late pullback-continuation confirmation: classify_m5_slot() only
    # recognizes confirmations within the origin M15 candle's own three
    # 5-minute sub-candles (900s). FRESH_PULLBACK_CONTINUATION is
    # DESIGNED to confirm later than that -- waiting for a genuine
    # pullback is the whole point of S2 -- so it legitimately produces
    # slot=None here. Previously this fell straight into the generic
    # "not in enabled_m5_slots" SKIP below and was silently, permanently
    # dropped every single time (confirmed directly against real data:
    # every FRESH_PULLBACK_CONTINUATION fire in a 371-day sample hit
    # this path). That's a routing bug, not the intended behavior of
    # S2's design. Handled as its own explicit, clearly-labeled case,
    # separate from the M5#1/#2/#3 slot scheme entirely -- gated behind
    # its OWN config flag (default OFF) because, unlike M5#1/M5#2, this
    # pattern has never been backtested for live trading.
    if slot is None and out.get("scenario") == "FRESH_PULLBACK_CONTINUATION":
        delay_s = event_ts - origin_ts if (event_ts is not None and origin_ts is not None) else None
        if not CONFIG.get("enable_late_pullback_fire", False):
            _attempted_thesis_ids.add(thesis_id)
            reason = (f"late pullback-continuation confirmation ({delay_s}s after M15 origin) -- "
                      f"enable_late_pullback_fire is False, this scenario type has not been "
                      f"backtested for live trading yet -- setup detected but skipped, not fired")
            logger.info(f"[scenario] thesis {thesis_id}: {reason}")
            try:
                db.save_scenario_watch(thesis_id, direction=out["direction"], origin_ts=origin_ts,
                                        origin_level=thesis_dbg["origin_level"], m5_slot=slot,
                                        status="skipped", reason=reason)
            except Exception:
                pass
            return {"action": "SKIPPED", "reason": reason, **log_base}
        # Enabled: fire immediately at the engine's own price, same
        # pattern as the existing M5#1/#3 immediate-fire behavior -- no
        # C timing, since that has only ever been validated for
        # confirmations within the origin candle's own window.
        _attempted_thesis_ids.add(thesis_id)
        reason = f"late pullback-continuation confirmed {delay_s}s after M15 origin (enable_late_pullback_fire=True)"
        try:
            db.save_scenario_watch(thesis_id, direction=out["direction"], origin_ts=origin_ts,
                                    origin_level=thesis_dbg["origin_level"], m5_slot=slot,
                                    status="fired", entry_ts=out["ts"], entry_price=out["entry"], reason=reason)
        except Exception:
            pass
        return {
            "action": "FIRE", "direction": out["direction"], "entry": out["entry"],
            "entry_ts": out["ts"], "atr15": out["debug"]["atr15"], "reason": reason, **log_base,
        }

    # Any slot not in enabled_m5_slots (M5#3 is excluded completely by
    # default -- see CONFIG["enabled_m5_slots"] comment). Detected and
    # logged, but explicitly never opened -- not a silent drop.
    _attempted_thesis_ids.add(thesis_id)
    reason = f"M5 slot {slot} is not in enabled_m5_slots {enabled_slots} -- setup detected but skipped, not fired"
    logger.info(f"[scenario] thesis {thesis_id}: {reason}")
    try:
        db.save_scenario_watch(thesis_id, direction=out["direction"], origin_ts=origin_ts,
                                origin_level=thesis_dbg["origin_level"], m5_slot=slot,
                                status="skipped", reason=reason)
    except Exception:
        pass
    return {"action": "SKIPPED", "reason": reason, **log_base}


def watcher_status() -> Dict:
    return {"active_c_watch_theses": _WATCHER.active_count(),
            "attempted_thesis_count": len(_attempted_thesis_ids)}