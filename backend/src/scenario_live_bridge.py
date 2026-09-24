"""Scenario live bridge.

Wires scenario_engine.py and entry_timing_c.py (C) into the live
evaluation loop. Decides only; real orders are created by the existing
execution path in autotrader_loop.py / autotrader_exec.py.

Selected as the live entry engine only when
autotrader_state.CONFIG["entry_engine"] == "scenario" (see
autotrader_loop.py).

Early-entry design (M15 candle 10:00-10:15, its 3 x M5 slots 10:00 /
10:05 / 10:10):

  S1  (scenario_engine FRESH_CLEAN_BREAKOUT)
      15M BOS/CHoCH seen inside the forming candle -> 5M BOS/CHoCH ->
      M1 close beyond both levels -> FIRE. The 5M break AND the M1 close
      must both happen in the FIRST 5 minutes of that M15 candle
      (10:00-10:05, slot 1). If the M15 candle then closes back inside,
      the trade is exited (autotrader_loop.py, rule b).
  S2  (scenario_engine FRESH_PULLBACK_CONTINUATION)
      15M BOS/CHoCH -> extension -> pullback -> fresh 5M BOS/CHoCH ->
      M1 close beyond both levels -> FIRE. Any time while the thesis is
      valid; the M1 close may come until the end of the M15 candle in
      which the 5M confirmation happened.
  C   M1 execution trigger (brain/entry_timing_c_watcher.py): checked on
      every M1 close in that window, continuously; a miss cancels that
      attempt only, never the thesis.

One attempt per engine execution event (fire_id), persisted in
scenario_c_watch so it survives restarts.
"""
import json
import time
from typing import Dict, Optional

from .config import ANALYSIS_LOOKBACK
from .market_data import data_access as dao
from .market_data import database as db
from .brain.scenario_engine import ScenarioEngine, m15_break_held
from .brain.entry_timing_c_watcher import EntryTimingCWatcher
from .autotrader_state import CONFIG, logger

_ENGINE = ScenarioEngine()
_WATCHER = EntryTimingCWatcher()

M15_SECONDS = 900
S1 = "FRESH_CLEAN_BREAKOUT"
S2 = "FRESH_PULLBACK_CONTINUATION"
SETUP_NAME = {S1: "S1", S2: "S2"}
# S1: 5M break and M1 close must be inside the FIRST 5 minutes (slot 1).
S1_SLOT = 1


def _restore_attempted_ids() -> set:
    """Restart safety for the duplicate-attempt guard: every attempt id
    ever recorded in scenario_c_watch (watching, fired, cancelled or
    skipped) has already been handled and is never attempted again. The
    DB-backed open-position check in autotrader_loop.py::evaluate()
    remains the real backstop against a duplicate live order."""
    try:
        return {row["thesis_id"] for row in db.load_all_scenario_watch()}
    except Exception:
        return set()


def _restore_pending_watches() -> Dict[str, Dict]:
    """Resume every M1 watch still marked 'watching' after a restart, so
    it keeps being checked until it fires, its window ends, or the thesis
    is invalidated."""
    pending: Dict[str, Dict] = {}
    try:
        for row in db.load_all_scenario_watch():
            if row.get("status") != "watching" or not row.get("meta"):
                continue
            pending[row["thesis_id"]] = json.loads(row["meta"])
    except Exception:
        pass
    return pending


_attempted_thesis_ids = _restore_attempted_ids()
# attempt_id -> watch info. Driven on EVERY evaluate_scenario() call: the
# engine reports FIRE for an execution event on one tick only.
_pending_watches: Dict[str, Dict] = _restore_pending_watches()


def _bridge_thesis_id(thesis_dbg: Dict) -> str:
    """Restart-stable thesis key. scenario_engine numbers theses from
    TH-000001 again on every process start, while the duplicate guard is
    restored from the DB -- suffixing the M15 origin candle's ts keeps
    keys unique per structural break."""
    return f"{thesis_dbg['thesis_id']}-{thesis_dbg['origin_ts']}"


def _m15_open(ts: float) -> int:
    return int(ts) - int(ts) % M15_SECONDS


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

    # The live bar is always the CURRENT clock 5m bucket, and only when
    # the previous 5m is already stored -- during a sync lag the live price
    # is never attributed to an older bucket (it would land in the wrong
    # M5 slot).
    forming_5m = None
    if candles_5m and live_price:
        now = int(time.time())
        forming_ts = now - now % 300
        if int(candles_5m[-1]["ts"]) + 300 == forming_ts:
            forming_5m = {"ts": forming_ts, "open": live_price, "high": live_price,
                           "low": live_price, "close": live_price, "volume": 0.0}

    return _ENGINE.tick(candles_15m[-200:], candles_5m[-200:], forming_5m)


def _skip(attempt_id: str, reason: str, base: Dict) -> Dict:
    logger.info(f"[scenario] {attempt_id}: {reason}")
    try:
        db.save_scenario_watch(attempt_id, direction=base["direction"], origin_ts=base["origin_ts"],
                               origin_level=base["origin_level"], m5_slot=base["m5_slot"],
                               status="skipped", reason=reason)
    except Exception:
        pass
    return {"action": "SKIPPED", "reason": reason, **base}


def evaluate_scenario(live_price: Optional[float]) -> Dict:
    """Returns a dict describing what happened; the caller
    (autotrader_loop.py) executes a "FIRE" result through the existing
    execution path -- this function never places orders."""
    out = _tick_engine(live_price)

    if out["action"] != "FIRE":
        return _drive_pending_watches(out)

    dbg = out.get("debug") or {}
    thesis_dbg = dbg.get("thesis")
    m5 = dbg.get("m5") or {}
    if thesis_dbg is None or m5.get("event_ts") is None:
        return {"action": "WAIT", "reason": "engine reported FIRE without thesis / M5 event -- refusing"}

    thesis_id = _bridge_thesis_id(thesis_dbg)
    fire_no = (out.get("fire_id") or "").split("/")[-1] or f"E{thesis_dbg.get('m5_event_id')}"
    attempt_id = f"{thesis_id}/{fire_no}"
    origin_ts = int(thesis_dbg["origin_ts"])
    m5_ts = int(m5["event_ts"])
    scenario = out.get("scenario")
    setup = SETUP_NAME.get(scenario)
    direction = out["direction"]
    now = time.time()

    if setup == "S1":
        slot = classify_m5_slot(origin_ts, m5_ts)
        window_end = origin_ts + 300 * S1_SLOT          # e.g. 10:05
    else:
        slot = classify_m5_slot(_m15_open(m5_ts), m5_ts)
        window_end = _m15_open(now) + M15_SECONDS       # end of this M15 candle

    levels = [float(thesis_dbg["origin_level"])]
    if m5.get("level") is not None:
        levels.append(float(m5["level"]))
    trigger_level = max(levels) if direction == "LONG" else min(levels)

    base = {
        "thesis_id": thesis_id, "attempt_id": attempt_id, "setup": setup,
        "direction": direction,
        "origin_event": thesis_dbg["origin_event"], "origin_level": thesis_dbg["origin_level"],
        "origin_ts": origin_ts, "provisional": bool(thesis_dbg.get("provisional")),
        "m5_event": m5.get("event"), "m5_level": m5.get("level"),
        "m5_confirmation_ts": m5_ts, "m5_slot": slot,
        "trigger_level": trigger_level, "start_ts": now, "window_end_ts": window_end,
        "scenario": scenario, "atr15": dbg.get("atr15"),
    }

    if attempt_id in _attempted_thesis_ids:
        if attempt_id in _pending_watches:
            return _drive_pending_watches(out)
        return {"action": "ALREADY_ATTEMPTED", "reason": f"{attempt_id} already attempted", **base}
    _attempted_thesis_ids.add(attempt_id)

    if setup is None:
        return _skip(attempt_id, f"engine FIRE with unknown scenario {scenario} -- not S1/S2", base)
    if any(w["thesis_id"] == thesis_id for w in _pending_watches.values()):
        return _skip(attempt_id, "an M1 watch for this thesis is already running", base)
    if setup == "S1" and slot != S1_SLOT:
        return _skip(attempt_id, f"S1 5M {m5.get('event')} was on M5 slot {slot}, "
                                 f"not the first 5 minutes of its M15 candle", base)

    _pending_watches[attempt_id] = base
    logger.info(f"[scenario] {setup} {attempt_id}: 5M {m5.get('event')} on slot {slot}; "
                f"watching M1 closes beyond {trigger_level} until {window_end}.")
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
    """Advance every pending M1 watch by one step. Returns the first
    FIRE/CANCEL it produces, else a WAIT describing the watch (or the
    engine's own WAIT when nothing is being watched)."""
    engine_thesis = (out.get("debug") or {}).get("thesis") or {}
    engine_key = (_bridge_thesis_id(engine_thesis)
                  if engine_thesis.get("thesis_id") and engine_thesis.get("origin_ts") is not None else None)
    waiting = None
    for attempt_id in list(_pending_watches):
        w = _pending_watches[attempt_id]

        # Thesis invalidated (structure broke, opposing CHoCH, or the M15
        # break did not hold on its close) while waiting for M1.
        if engine_key == w["thesis_id"] and engine_thesis.get("status") == "INVALIDATED":
            why = engine_thesis.get("invalid_reason") or out.get("scenario")
            return _cancel_pending(attempt_id, f"thesis invalidated while watching M1 ({why})")

        c_result = _WATCHER.check(
            attempt_id, w["direction"], w["trigger_level"], w["start_ts"], w["window_end_ts"],
            origin_ts=w["origin_ts"], origin_level=w["origin_level"],
            m5_slot=w["m5_slot"], meta=json.dumps(w),
        )
        if c_result is None:
            waiting = waiting or {"action": "WAIT", "reason": f"{w['setup']}: watching M1 closes beyond "
                                                             f"{w['trigger_level']}", **w}
            continue
        _pending_watches.pop(attempt_id, None)
        if c_result.get("cancelled"):
            logger.warning(f"[scenario] {attempt_id}: CANCELLED -- {c_result['reason']}")
            return {"action": "CANCEL", "reason": c_result["reason"], **w}

        reason = (f"C: M1 closed beyond {w['trigger_level']} (M15 level + 5M {w['m5_event']} level), "
                  f"{w['setup']}")
        logger.info(f"[scenario] {attempt_id}: FIRE -- {reason}")
        return {
            **w, "action": "FIRE", "entry": c_result["entry_price"], "entry_ts": c_result["entry_ts"],
            "c_intended_price": c_result["entry_price"], "c_intended_ts": c_result["entry_ts"],
            "reason": reason,
            # A watch resumed after a restart may lack ATR; use the
            # engine's current M15 ATR so sizing never runs blind.
            "atr15": w.get("atr15") or (out.get("debug") or {}).get("atr15"),
        }

    if waiting is not None:
        return waiting
    return {"action": "WAIT", "reason": out.get("what_happening"), "scenario": out.get("scenario")}


def break_failed(origin_ts: int, direction: str) -> Optional[bool]:
    """Rule (b) for an S1 trade entered before its M15 candle closed:
    True = that candle closed back inside (exit), False = the break held,
    None = the candle has not closed / is not in storage yet."""
    candles_15m = dao.read_closed_candles("15m", limit=ANALYSIS_LOOKBACK)
    if not candles_15m or int(candles_15m[-1]["ts"]) < int(origin_ts):
        return None
    if not any(int(c["ts"]) == int(origin_ts) for c in candles_15m):
        return None
    return m15_break_held(candles_15m, origin_ts, direction) is None


def watcher_status() -> Dict:
    return {"active_c_watch_theses": _WATCHER.active_count(),
            "pending_watches": sorted(_pending_watches),
            "attempted_count": len(_attempted_thesis_ids)}
