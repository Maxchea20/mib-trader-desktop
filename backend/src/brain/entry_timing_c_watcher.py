"""Entry-Timing C live watcher.

C is the M1 execution trigger after an S1/S2 M5 confirmation:
first fully closed 1-minute candle whose close is beyond the M15 level.
Window is the SAME M15 candle (3 x M5 = 15 x M1), from the M5 confirm
until that 15m ends. A miss cancels the attempt only, not the thesis.
"""
import json
import time
from typing import Dict, Optional

from ..market_data import data_access as dao
from ..market_data import database as db
from .entry_timing_c import find_m5_2_intrabar_entry

M15_SECONDS = 900
SYNC_GRACE_SECONDS = 120


class EntryTimingCWatcher:
    def __init__(self):
        self._state: Dict[str, dict] = {}
        self._restore_from_db()

    def _restore_from_db(self) -> None:
        try:
            for row in db.load_all_scenario_watch():
                if row.get("status") != "watching":
                    continue
                checked = set(json.loads(row["checked_ts"])) if row.get("checked_ts") else set()
                self._state[row["thesis_id"]] = {
                    "checked_ts": checked,
                    "started_at": row.get("started_at") or time.time(),
                }
        except Exception:
            pass

    def _persist(self, watch_id: str, **fields) -> None:
        state = self._state.get(watch_id, {})
        try:
            db.save_scenario_watch(
                watch_id, checked_ts=json.dumps(sorted(state.get("checked_ts", set()))),
                started_at=state.get("started_at"), **fields,
            )
        except Exception:
            pass

    def active_count(self) -> int:
        return len(self._state)

    def forget(self, watch_id: str) -> None:
        self._state.pop(watch_id, None)

    def check(self, watch_id: str, direction: str, structural_level: float,
              window_open_ts: int, start_ts: float, slot: Optional[int] = None,
              origin_ts: Optional[int] = None) -> Optional[dict]:
        window_end = window_open_ts + M15_SECONDS
        first_minute = max(window_open_ts, int(start_ts) - int(start_ts) % 60)
        minutes = list(range(first_minute, window_end, 60))

        is_new = watch_id not in self._state
        state = self._state.setdefault(watch_id, {"checked_ts": set(), "started_at": float(start_ts)})
        if is_new:
            self._persist(watch_id, direction=direction, origin_ts=origin_ts,
                          origin_level=structural_level, m5_slot=slot, status="watching",
                          window_open_ts=window_open_ts,
                          reason=f"watch started (M1 continuous to M15 close {window_end})")

        def cancel(reason: str) -> dict:
            self.forget(watch_id)
            self._persist(watch_id, status="cancelled", reason=reason)
            return {"cancelled": True, "reason": reason}

        if not minutes:
            return cancel("M5 confirmation came after this M15 candle ended -- no M1 window left")

        now = time.time()
        due = [t for t in minutes if t + 60 <= now]
        if not due:
            return None

        by_ts = {c["ts"]: c for c in dao.read_closed_candles("1m", limit=400)}
        candles = []
        for t in due:
            row = by_ts.get(t)
            if row is None:
                if now > t + 60 + SYNC_GRACE_SECONDS:
                    return cancel(f"M1 candle at ts={t} past its close but not in storage -- sync gap")
                break
            candles.append(row)

        new_ts = {c["ts"] for c in candles if c["ts"] not in state["checked_ts"]}
        if not new_ts:
            return None
        state["checked_ts"].update(new_ts)
        self._persist(watch_id, status="watching",
                      reason=f"checked {len(state['checked_ts'])}/{len(minutes)} M1 candles")

        result = find_m5_2_intrabar_entry(direction, structural_level, first_minute, candles)
        if result is not None:
            self.forget(watch_id)
            self._persist(watch_id, status="fired", entry_ts=result["entry_ts"],
                          entry_price=result["entry_price"],
                          reason=f"M1 close confirmed at minute {result['confirmed_at_minute']}")
            return result

        if len(candles) == len(minutes):
            return cancel("M15 candle ended with no M1 close beyond the structural level")
        return None
