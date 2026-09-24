"""Scenario live bridge — same-15m slots 1/2/3, fire before 15m close.

S1: M15 BOS/CHoCH (forming candle allowed) + M5 confirm in slot 1 or 2
of THAT same 15m -> C (M1 close beyond level) -> FIRE.
S2: pullback continuation, no slot cap, C on the 15m that confirmed.
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

M15_SECONDS = 900
S1 = "FRESH_CLEAN_BREAKOUT"
S2 = "FRESH_PULLBACK_CONTINUATION"
SETUP_NAME = {S1: "S1", S2: "S2"}


def _restore_attempted_ids() -> set:
    try:
        return {row["thesis_id"] for row in db.load_all_scenario_watch()}
    except Exception:
        return set()


def _restore_pending_watches() -> Dict[str, Dict]:
    pending: Dict[str, Dict] = {}
    try:
        for row in db.load_all_scenario_watch():
            if row.get("status") != "watching" or row.get("window_open_ts") is None:
                continue
            attempt_id = row["thesis_id"]
            pending[attempt_id] = {
                "attempt_id": attempt_id, "thesis_id": attempt_id.split("/")[0],
                "direction": row["direction"], "origin_level": row["origin_level"],
                "origin_ts": row["origin_ts"], "m5_slot": row.get("m5_slot"),
                "window_open_ts": int(row["window_open_ts"]),
                "start_ts": row.get("started_at") or time.time(),
            }
    except Exception:
        pass
    return pending


_attempted_thesis_ids = _restore_attempted_ids()
_pending_watches: Dict[str, Dict] = _restore_pending_watches()


def _bridge_thesis_id(thesis_dbg: Dict) -> str:
    return f"{thesis_dbg['thesis_id']}-{thesis_dbg['origin_ts']}"


def _m15_open(ts: int) -> int:
    return int(ts) - int(ts) % M15_SECONDS


def classify_m5_slot(origin_ts: Optional[int], event_ts: Optional[int]) -> Optional[int]:
    if origin_ts is None or event_ts is None:
        return None
    delta = event_ts - origin_ts
    if delta < 0 or delta >= M15_SECONDS:
        return None
    return int(delta // 300) + 1


def _tick_engine(live_price: Optional[float]) -> Dict:
    candles_15m = dao.read_closed_candles("15m", limit=ANALYSIS_LOOKBACK)
    candles_5m = dao.read_closed_candles("5m", limit=ANALYSIS_LOOKBACK)
    if len(candles_15m) < 60 or len(candles_5m) < 60:
        return {"action": "WAIT", "reason": "insufficient candle history", "debug": {"thesis": None}}
    forming_5m = None
    if live_price:
        now = int(time.time())
        forming_ts = now - now % 300
        if int(candles_5m[-1]["ts"]) + 300 == forming_ts:
            forming_5m = {"ts": forming_ts, "open": live_price, "high": live_price,
                          "low": live_price, "close": live_price, "volume": 0.0}
    return _ENGINE.tick(candles_15m[-200:], candles_5m[-200:], forming_5m)


def _skip(attempt_id: str, reason: str, log_base: Dict) -> Dict:
    logger.info(f"[scenario] {attempt_id}: {reason}")
    try:
        db.save_scenario_watch(attempt_id, direction=log_base["direction"],
                               origin_ts=log_base["origin_ts"], origin_level=log_base["origin_level"],
                               m5_slot=log_base["m5_slot"], status="skipped", reason=reason)
    except Exception:
        pass
    return {"action": "SKIPPED", "reason": reason, **log_base}


def evaluate_scenario(live_price: Optional[float]) -> Dict:
    out = _tick_engine(live_price)
    if out["action"] != "FIRE":
        return _drive_pending_watches(out)
    thesis_dbg = (out.get("debug") or {}).get("thesis")
    if thesis_dbg is None:
        return {"action": "WAIT", "reason": "engine reported FIRE with no thesis debug -- refusing"}
    thesis_id = _bridge_thesis_id(thesis_dbg)
    fire_no = (out.get("fire_id") or "").split("/")[-1] or f"E{thesis_dbg.get('m5_event_id')}"
    attempt_id = f"{thesis_id}/{fire_no}"
    origin_ts = thesis_dbg["origin_ts"]
    event_ts = int(out["ts"])
    scenario = out.get("scenario")
    setup = SETUP_NAME.get(scenario)
    if setup == "S1":
        slot = classify_m5_slot(origin_ts, event_ts)
        window_open = origin_ts
    else:
        window_open = _m15_open(event_ts)
        slot = classify_m5_slot(window_open, event_ts)
    log_base = {
        "thesis_id": thesis_id, "attempt_id": attempt_id, "setup": setup,
        "direction": out["direction"],
        "origin_event": thesis_dbg["origin_event"], "origin_level": thesis_dbg["origin_level"],
        "origin_ts": origin_ts, "m5_confirmation_ts": event_ts, "m5_slot": slot,
        "window_open_ts": window_open, "provisional": thesis_dbg.get("provisional"),
        "scenario": scenario, "atr15": (out.get("debug") or {}).get("atr15"),
    }
    if attempt_id in _attempted_thesis_ids:
        if attempt_id in _pending_watches:
            return _drive_pending_watches(out)
        return {"action": "ALREADY_ATTEMPTED", "reason": f"{attempt_id} already attempted", **log_base}
    _attempted_thesis_ids.add(attempt_id)
    if setup is None:
        return _skip(attempt_id, f"engine FIRE with unknown scenario {scenario} -- not S1/S2", log_base)
    if any(w["thesis_id"] == thesis_id for w in _pending_watches.values()):
        return _skip(attempt_id, "an M1 watch for this thesis is already running", log_base)
    if setup == "S1":
        enabled_slots = CONFIG.get("enabled_m5_slots", [1, 2])
        if slot is None:
            return _skip(attempt_id, "S1 M5 confirm outside the 3 slots of its own 15m", log_base)
        if slot not in enabled_slots:
            return _skip(attempt_id, f"S1 M5 slot {slot} not in {enabled_slots}", log_base)
    _pending_watches[attempt_id] = {**log_base, "start_ts": time.time()}
    logger.info(f"[scenario] {setup} {attempt_id} M5#{slot}: watching M1 until 15m close")
    return _drive_pending_watches(out)


def _cancel_pending(attempt_id: str, reason: str) -> Dict:
    base = _pending_watches.pop(attempt_id)
    _WATCHER.forget(attempt_id)
    try:
        db.save_scenario_watch(attempt_id, status="cancelled", reason=reason)
    except Exception:
        pass
    logger.warning(f"[scenario] {attempt_id}: CANCELLED -- {reason}")
    return {"action": "CANCEL", "reason": reason, **base}


def _drive_pending_watches(out: Dict) -> Dict:
    engine_thesis = (out.get("debug") or {}).get("thesis") or {}
    engine_key = (_bridge_thesis_id(engine_thesis)
                  if engine_thesis.get("thesis_id") and engine_thesis.get("origin_ts") is not None else None)
    waiting = None
    for attempt_id in list(_pending_watches):
        w = _pending_watches[attempt_id]
        if engine_key == w["thesis_id"] and engine_thesis.get("status") == "INVALIDATED":
            why = engine_thesis.get("invalid_reason") or out.get("scenario")
            return _cancel_pending(attempt_id, f"thesis invalidated while watching M1 ({why})")
        c_result = _WATCHER.check(attempt_id, w["direction"], w["origin_level"],
                                  w["window_open_ts"], w["start_ts"], slot=w.get("m5_slot"),
                                  origin_ts=w["origin_ts"])
        if c_result is None:
            waiting = waiting or {"action": "WAIT",
                                  "reason": f"{w.get('setup') or ''} M5#{w.get('m5_slot')}: watching M1 until 15m close".strip(),
                                  **w}
            continue
        _pending_watches.pop(attempt_id, None)
        if c_result.get("cancelled"):
            logger.warning(f"[scenario] {attempt_id}: CANCELLED -- {c_result['reason']}")
            return {"action": "CANCEL", "reason": c_result["reason"], **w}
        reason = (f"C: M1 closed beyond {w['origin_level']} ({w.get('setup')}, M5#{w.get('m5_slot')})")
        logger.info(f"[scenario] {attempt_id}: FIRE -- {reason}")
        return {
            **w, "action": "FIRE", "entry": c_result["entry_price"], "entry_ts": c_result["entry_ts"],
            "c_intended_price": c_result["entry_price"], "c_intended_ts": c_result["entry_ts"],
            "reason": reason,
            "atr15": w.get("atr15") or (out.get("debug") or {}).get("atr15"),
        }
    if waiting is not None:
        return waiting
    return {"action": "WAIT", "reason": out.get("what_happening"), "scenario": out.get("scenario"),
            "thesis_id": engine_key, "direction": engine_thesis.get("direction"),
            "provisional": engine_thesis.get("provisional")}


def watcher_status() -> Dict:
    return {"active_c_watch_theses": _WATCHER.active_count(),
            "pending_watches": sorted(_pending_watches),
            "attempted_count": len(_attempted_thesis_ids)}
