"""Scenario live bridge. NEW module.

Wires scenario_engine.py (UNCHANGED) and entry_timing_c.py (UNCHANGED)
into the existing live evaluation loop, lifecycle, and execution
machinery. Does not touch scenario_engine.py's internals, does not
touch entry_timing_c.py's internals, does not duplicate lifecycle.py's
SL/TP/position logic (uses lifecycle.position_from_scenario_fire, the
thin adapter added alongside this), and does not duplicate
autotrader_exec.py's order mechanics.

This is the ONLY live entry engine (the legacy Hunt C-FI FIRE path was
removed -- see autotrader_loop.py). This module is the only thing
permitted to open a NEW trade; once open, management reverts entirely
to the existing, unchanged brain/lifecycle_tick.py machinery.

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


def _restore_pending_watches() -> Dict[str, Dict]:
    """Restart safety for in-flight M1 watches: every row still marked
    'watching' is resumed, so the watcher keeps being driven (and will
    confirm, or time out and cancel) instead of being silently dropped."""
    pending: Dict[str, Dict] = {}
    try:
        for row in db.load_all_scenario_watch():
            if row.get("status") != "watching" or row.get("m5_slot") is None:
                continue
            pending[row["thesis_id"]] = {
                "thesis_id": row["thesis_id"], "direction": row["direction"],
                "origin_level": row["origin_level"], "origin_ts": row["origin_ts"],
                "m5_slot": int(row["m5_slot"]),
            }
    except Exception:
        pass
    return pending


# thesis_id -> log_base of every thesis whose M1 confirmation is still
# being watched. Driven on EVERY evaluate_scenario() call, not only on the
# single tick the engine reported FIRE (the engine spends its execution
# event on that tick, so it never reports FIRE for it again).
_pending_watches: Dict[str, Dict] = _restore_pending_watches()


M15_SECONDS = 900


def _bridge_thesis_id(thesis_dbg: Dict) -> str:
    """Restart-stable thesis key. scenario_engine numbers theses from
    TH-000001 again on every process start, while _attempted_thesis_ids
    is restored from the DB -- so the bare engine id collides with an
    older, unrelated thesis after a restart and the new one is wrongly
    refused as ALREADY_ATTEMPTED. Suffixing the M15 origin candle's ts
    makes the key unique per structural break (and the same break seen
    again after a restart still maps to the same key)."""
    return f"{thesis_dbg['thesis_id']}-{thesis_dbg['origin_ts']}"


def classify_m5_slot(origin_ts: Optional[int], event_ts: Optional[int]) -> Optional[int]:
    """Which of the 3 five-minute candles AFTER the thesis's M15 candle
    closed does event_ts fall on? (1/2/3, else None.)

    origin_ts is the OPEN time of the M15 candle that broke structure
    (candles are timestamped by open). The thesis only exists once that
    candle has closed, i.e. at origin_ts + 900 -- so the first M5 candle
    that can ever confirm it opens at origin_ts + 900, not origin_ts.
    Counting from origin_ts put every live confirmation at delta >= 900
    and so outside every slot, which meant nothing could ever fire."""
    if origin_ts is None or event_ts is None:
        return None
    delta = event_ts - (origin_ts + M15_SECONDS)
    if delta < 0 or delta >= M15_SECONDS:
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
        return _drive_pending_watches(out)

    thesis_dbg = out.get("debug", {}).get("thesis")
    if thesis_dbg is None:
        return {"action": "WAIT", "reason": "engine reported FIRE with no thesis debug -- refusing, should not happen"}

    thesis_id = _bridge_thesis_id(thesis_dbg)
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
        # Normal while its M1 watch is still pending -- keep driving it.
        if thesis_id in _pending_watches:
            return _drive_pending_watches(out)
        return {"action": "ALREADY_ATTEMPTED", "reason": f"thesis {thesis_id} already attempted", **log_base}

    enabled_slots = CONFIG.get("enabled_m5_slots", [1, 2])
    _attempted_thesis_ids.add(thesis_id)

    if slot is not None and slot in enabled_slots:
        # Same C mechanism, same watcher, same find_m5_2_intrabar_entry()
        # rule for every enabled slot -- only the window watched differs
        # (that slot's own 5-minute window). Which slots are eligible is
        # controlled entirely by CONFIG["enabled_m5_slots"].
        _pending_watches[thesis_id] = log_base
        logger.info(f"[scenario] M5#{slot} thesis {thesis_id}: watching M1 for confirming close.")
        return _drive_pending_watches(out)

    # Any slot not in enabled_m5_slots (M5#3 is excluded completely by
    # default -- see CONFIG["enabled_m5_slots"] comment), or a
    # confirmation later than M5#3. Detected and logged, but explicitly
    # never opened -- not a silent drop.
    reason = f"M5 slot {slot} is not in enabled_m5_slots {enabled_slots} -- setup detected but skipped, not fired"
    logger.info(f"[scenario] thesis {thesis_id}: {reason}")
    try:
        db.save_scenario_watch(thesis_id, direction=out["direction"], origin_ts=origin_ts,
                                origin_level=thesis_dbg["origin_level"], m5_slot=slot,
                                status="skipped", reason=reason)
    except Exception:
        pass
    return {"action": "SKIPPED", "reason": reason, **log_base}


def _cancel_pending(thesis_id: str, reason: str) -> Dict:
    base = _pending_watches.pop(thesis_id)
    _WATCHER.forget(thesis_id)
    try:
        db.save_scenario_watch(thesis_id, status="cancelled", reason=reason)
    except Exception:
        pass
    logger.warning(f"[scenario] M5#{base['m5_slot']} thesis {thesis_id}: CANCELLED -- {reason}")
    return {"action": "CANCEL", "reason": reason, **base}


def _drive_pending_watches(out: Dict) -> Dict:
    """Advance every pending M1 watch by one step. Returns the first
    FIRE/CANCEL it produces, else a WAIT describing the watch (or the
    engine's own WAIT when nothing is being watched)."""
    engine_thesis = (out.get("debug") or {}).get("thesis") or {}
    waiting = None
    for thesis_id in list(_pending_watches):
        base = _pending_watches[thesis_id]
        slot = base["m5_slot"]

        # The engine has invalidated this thesis (structure broke, or an
        # opposing CHoCH) while we were still waiting for the M1 close.
        if (engine_thesis.get("thesis_id") and engine_thesis.get("origin_ts") is not None
                and _bridge_thesis_id(engine_thesis) == thesis_id
                and engine_thesis.get("status") == "INVALIDATED"):
            return _cancel_pending(thesis_id, f"thesis invalidated while watching M5#{slot} "
                                              f"({out.get('scenario')})")

        c_result = _WATCHER.check(thesis_id, base["direction"], base["origin_level"],
                                  base["origin_ts"], slot)
        if c_result is None:
            waiting = waiting or {"action": "WAIT",
                                  "reason": f"M5#{slot}: watching M1 for confirming close", **base}
            continue
        if c_result.get("cancelled"):
            _pending_watches.pop(thesis_id, None)
            logger.warning(f"[scenario] M5#{slot} thesis {thesis_id}: CANCELLED -- {c_result['reason']}")
            return {"action": "CANCEL", "reason": c_result["reason"], **base}

        _pending_watches.pop(thesis_id, None)
        entry_price = c_result["entry_price"]
        entry_ts = c_result["entry_ts"]
        reason = (f"C: M1 close confirmed at minute {c_result['confirmed_at_minute']} of M5#{slot} "
                  f"({c_result['seconds_after_m5_2_open']}s after M5#{slot} open)")
        logger.info(f"[scenario] M5#{slot} thesis {thesis_id}: FIRE -- {reason}")
        return {
            "action": "FIRE", "entry": entry_price, "entry_ts": entry_ts,
            "c_intended_price": entry_price, "c_intended_ts": entry_ts,
            "reason": reason, **base,
            # A watch resumed after a restart has no stored ATR; use the
            # engine's current M15 ATR so sizing never runs blind.
            "atr15": base.get("atr15") or (out.get("debug") or {}).get("atr15"),
        }

    if waiting is not None:
        return waiting
    return {"action": "WAIT", "reason": out.get("what_happening"), "scenario": out.get("scenario")}


def watcher_status() -> Dict:
    return {"active_c_watch_theses": _WATCHER.active_count(),
            "pending_watch_theses": sorted(_pending_watches),
            "attempted_thesis_count": len(_attempted_thesis_ids)}
