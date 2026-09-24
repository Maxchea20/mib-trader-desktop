"""Blueprint S1 / S2 / S3 live bridge.

S1: M15 BOS/CHoCH -> M5 BOS/CHoCH in the SAME 15m (slots 1/2) -> FIRE. No M1.
S2: M15 BOS/CHoCH -> extension -> pullback -> fresh M5 -> FIRE. No M1.
S3: M5 exhaustion context -> M5 reversal BOS/CHoCH -> later M1 -> FIRE.
    M15 BOS/CHoCH is not required.

FIRE = scenario condition satisfied. Orders stay in autotrader_loop.
"""
import time
from typing import Dict, Optional

from .config import ANALYSIS_LOOKBACK
from .market_data import data_access as dao
from .market_data import database as db
from .brain.scenario_engine import ScenarioEngine
from .brain.s3_early_reversal import (
    ReversalOwnership, detect_candidate, check_m1_confirmation, candidate_to_result,
)
from .autotrader_state import CONFIG, logger

_ENGINE = ScenarioEngine()
_OWN = ReversalOwnership()
_S3_PENDING = None

M15_SECONDS = 900
S1 = "FRESH_CLEAN_BREAKOUT"
S2 = "FRESH_PULLBACK_CONTINUATION"
SETUP_NAME = {S1: "S1", S2: "S2"}


def _restore_attempted() -> set:
    try:
        return {row["thesis_id"] for row in db.load_all_scenario_watch()}
    except Exception:
        return set()


_attempted = _restore_attempted()


def _key(thesis_dbg: Dict) -> str:
    return f"{thesis_dbg['thesis_id']}-{thesis_dbg['origin_ts']}"


def classify_m5_slot(origin_ts: Optional[int], event_ts: Optional[int]) -> Optional[int]:
    if origin_ts is None or event_ts is None:
        return None
    delta = event_ts - origin_ts
    if delta < 0 or delta >= M15_SECONDS:
        return None
    return int(delta // 300) + 1


def _tick_engine(live_price: Optional[float]) -> Dict:
    c15 = dao.read_closed_candles("15m", limit=ANALYSIS_LOOKBACK)
    c5 = dao.read_closed_candles("5m", limit=ANALYSIS_LOOKBACK)
    if len(c15) < 60 or len(c5) < 60:
        return {"action": "WAIT", "reason": "insufficient candle history", "debug": {"thesis": None}}
    forming_5m = None
    if live_price:
        now = int(time.time())
        forming_ts = now - now % 300
        if int(c5[-1]["ts"]) + 300 == forming_ts:
            forming_5m = {
                "ts": forming_ts, "open": live_price, "high": live_price,
                "low": live_price, "close": live_price, "volume": 0.0,
            }
    return _ENGINE.tick(c15[-200:], c5[-200:], forming_5m)


def _mark(attempt_id: str, status: str, log_base: Dict, reason: str) -> None:
    _attempted.add(attempt_id)
    try:
        db.save_scenario_watch(
            attempt_id, direction=log_base.get("direction"),
            origin_ts=log_base.get("origin_ts"), origin_level=log_base.get("origin_level"),
            m5_slot=log_base.get("m5_slot"), status=status, reason=reason,
        )
    except Exception:
        pass


def _s1_s2_from_engine(out: Dict) -> Optional[Dict]:
    if out.get("action") != "FIRE":
        return None
    thesis_dbg = (out.get("debug") or {}).get("thesis")
    if not thesis_dbg:
        return None
    scenario = out.get("scenario")
    setup = SETUP_NAME.get(scenario)
    if setup is None:
        return None
    origin_ts = thesis_dbg["origin_ts"]
    event_ts = int(out["ts"])
    slot = classify_m5_slot(origin_ts, event_ts)
    fire_no = (out.get("fire_id") or "").split("/")[-1] or f"E{thesis_dbg.get('m5_event_id')}"
    attempt_id = f"{_key(thesis_dbg)}/{fire_no}"
    log_base = {
        "thesis_id": _key(thesis_dbg), "attempt_id": attempt_id, "setup": setup,
        "direction": out["direction"], "origin_event": thesis_dbg["origin_event"],
        "origin_level": thesis_dbg["origin_level"], "origin_ts": origin_ts,
        "m5_confirmation_ts": event_ts, "m5_slot": slot, "scenario": scenario,
        "atr15": (out.get("debug") or {}).get("atr15"),
        "entry": out.get("entry") or thesis_dbg.get("origin_level"),
        "entry_ts": event_ts,
    }
    if attempt_id in _attempted:
        return {"action": "ALREADY_ATTEMPTED", "reason": f"{attempt_id} already attempted", **log_base}
    if setup == "S1":
        enabled = CONFIG.get("enabled_m5_slots", [1, 2])
        if slot is None:
            _mark(attempt_id, "skipped", log_base, "S1 M5 is outside its own 15m window")
            return {"action": "SKIPPED", "reason": "S1 M5 outside its own 15m window", **log_base}
        if slot not in enabled:
            _mark(attempt_id, "skipped", log_base, f"S1 slot {slot} not in {enabled}")
            return {"action": "SKIPPED", "reason": f"S1 slot {slot} not in {enabled}", **log_base}
    reason = f"{setup} M15 {thesis_dbg['origin_event']} + M5 confirm (slot {slot}) — FIRE, no M1"
    _mark(attempt_id, "fired", log_base, reason)
    logger.info(f"[scenario] {reason}")
    return {
        **log_base, "action": "FIRE", "reason": reason,
        "c_intended_price": log_base["entry"], "c_intended_ts": event_ts,
    }


def _drive_s3(out: Dict, live_price: Optional[float]) -> Optional[Dict]:
    if not CONFIG.get("s3_enabled", True):
        return None
    global _S3_PENDING
    c15 = dao.read_closed_candles("15m", limit=ANALYSIS_LOOKBACK)
    c5 = dao.read_closed_candles("5m", limit=ANALYSIS_LOOKBACK)
    c1 = dao.read_closed_candles("1m", limit=200)
    thesis = (out.get("debug") or {}).get("thesis") or {}
    cur15 = int(c15[-1]["ts"]) if c15 else None
    m15_fresh = bool(
        thesis.get("origin_ts") is not None
        and cur15 is not None
        and int(thesis["origin_ts"]) == cur15
        and out.get("scenario") not in (None, "NO_SETUP")
    )
    if _S3_PENDING is None:
        cand = detect_candidate(c15, c5, m15_fresh, _OWN)
        if cand is None:
            return None
        _S3_PENDING = cand
        logger.info(f"[scenario] S3 candidate M5 {cand.origin_event} {cand.direction} — waiting later M1")
        return {
            "action": "WAIT", "setup": "S3", "scenario": "S3_EARLY_REVERSAL",
            "reason": f"S3 M5 {cand.origin_event} printed — waiting later M1 confirm",
            "direction": cand.direction, "origin_ts": cand.origin_ts,
        }
    cand = _S3_PENDING
    if c1 and int(c1[-1]["ts"]) > int(cand.origin_ts) and check_m1_confirmation(cand, c1):
        _S3_PENDING = None
        _OWN.own(cand.direction, cand.origin_level, cand.origin_ts)
        price = float(live_price or cand.origin_level)
        result = candidate_to_result(cand, price, f"S3-{cand.origin_ts}")
        result["setup"] = "S3"
        result["entry_ts"] = cand.m1_confirm_ts
        result["c_intended_price"] = price
        result["c_intended_ts"] = cand.m1_confirm_ts
        logger.info("[scenario] S3 FIRE after later M1 confirm")
        return result
    return {
        "action": "WAIT", "setup": "S3", "scenario": "S3_EARLY_REVERSAL",
        "reason": "S3 waiting later M1 directional confirm",
        "direction": cand.direction,
    }


def evaluate_scenario(live_price: Optional[float]) -> Dict:
    out = _tick_engine(live_price)
    hit = _s1_s2_from_engine(out)
    if hit is not None:
        return hit
    s3 = _drive_s3(out, live_price)
    if s3 is not None:
        return s3
    return {
        "action": "WAIT",
        "reason": out.get("what_happening"),
        "scenario": out.get("scenario"),
        "provisional": ((out.get("debug") or {}).get("thesis") or {}).get("provisional"),
        "direction": out.get("direction"),
    }


def watcher_status() -> Dict:
    return {"s3_pending": _S3_PENDING is not None, "attempted_count": len(_attempted)}
