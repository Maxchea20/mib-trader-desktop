"""Entry-Timing C live watcher.

After S1/S2 M5 confirm, watch the FIVE 1-minute candles INSIDE that M5.
Enter on the first closed 1m whose close crosses the structural level.
Not fixed to minute 1 or 3. Minutes 1-5 are equal. A miss cancels this
attempt only.
"""
import json
import time
from typing import Dict, Optional

from ..market_data import data_access as dao
from ..market_data import database as db
from .entry_timing_c import find_m5_2_intrabar_entry

M5_SECONDS = 300
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
        minutes = [int(window_open_ts) + i * 60 for i in range(5)]

        is_new = watch_id not in self._state
        state = self._state.setdefault(watch_id, {"checked_ts": set(), "started_at": float(start_ts)})
        if is_new:
            self._persist(watch_id, direction=direction, origin_ts=origin_ts,
                          origin_level=structural_level, m5_slot=slot, status="watching",
                          window_open_ts=window_open_ts,
                          reason=f"C watch: 5x 1m inside M5 {window_open_ts}")

        def cancel(reason: str) -> dict:
            self.forget(watch_id)
            self._persist(watch_id, status="cancelled", reason=reason)
            return {"cancelled": True, "reason": reason}

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
                    return cancel(f"M1 candle at ts={t} past close but not in storage -- sync gap")
                break
            candles.append(row)

        new_ts = {c["ts"] for c in candles if c["ts"] not in state["checked_ts"]}
        if not new_ts:
            return None
        state["checked_ts"].update(new_ts)
        self._persist(watch_id, status="watching",
                      reason=f"checked {len(state['checked_ts'])}/5 M1 candles")

        result = find_m5_2_intrabar_entry(direction, structural_level, int(window_open_ts), candles)
        if result is not None:
            self.forget(watch_id)
            self._persist(watch_id, status="fired", entry_ts=result["entry_ts"],
                          entry_price=result["entry_price"],
                          reason=f"C: first 1m close at minute {result['confirmed_at_minute']} of this M5")
            return result

        if len(candles) >= 5:
            return cancel("all 5 M1 closes of this M5 checked, none crossed the level")
        return None
