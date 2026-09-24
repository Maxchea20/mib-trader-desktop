"""Entry-Timing C live watcher.

C is the M1 execution trigger that runs AFTER an S1/S2 M5 confirmation:
it fires on the first fully closed 1-minute candle whose close is beyond
the thesis's M15 structural level (the unmodified rule in
src/brain/entry_timing_c.py::find_m5_2_intrabar_entry()).

Window (per watch):
  - It belongs to ONE M15 candle: window_open_ts .. window_open_ts + 900,
    i.e. that candle's own 3 x M5 = 15 x M1 candles.
  - It is checked CONTINUOUSLY, from the M1 candle in which the M5
    confirmation happened (start_ts) until that M15 candle ends -- not
    only one M5 slot's 5 minutes, and not a one-time check.
  - If the M15 candle ends with no qualifying M1 close, the watch is
    CANCELLED (that attempt only -- the thesis itself is untouched).

Guarantees, by construction:
  - Idempotent: re-checking already-seen candles is a cheap no-op.
  - Never fabricates a candle from live tick price and never looks at a
    still-forming candle.
  - Never falls back to another entry on missing data. A candle that is
    past its close but still not in storage after SYNC_GRACE_SECONDS is a
    real sync gap -> CANCEL, with the reason stated. Before that grace it
    is simply "not synced yet" and the watch keeps waiting.
  - Restart-safe: watching rows are persisted in scenario_c_watch.
"""
import json
import time
from typing import Dict, Optional

from ..market_data import data_access as dao
from ..market_data import database as db
from .entry_timing_c import find_m5_2_intrabar_entry

M15_SECONDS = 900
# How long after a 1m candle's close we wait for it to appear in storage
# (the candle sync runs on a ~15-60s cadence) before calling it a gap.
SYNC_GRACE_SECONDS = 120


class EntryTimingCWatcher:
    def __init__(self):
        # watch_id -> {"checked_ts": set(...), "started_at": float}
        self._state: Dict[str, dict] = {}
        self._restore_from_db()

    def _restore_from_db(self) -> None:
        """Reload every watch still marked 'watching' so its checked
        candles are not re-evaluated after a restart. The bridge resumes
        driving these rows (see scenario_live_bridge._restore_pending)."""
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
            # Restart safety must never crash startup.
            pass

    def _persist(self, watch_id: str, **fields) -> None:
        state = self._state.get(watch_id, {})
        try:
            db.save_scenario_watch(
                watch_id, checked_ts=json.dumps(sorted(state.get("checked_ts", set()))),
                started_at=state.get("started_at"), **fields,
            )
        except Exception:
            pass  # persistence is a safety net, never a hard dependency for a live tick

    def active_count(self) -> int:
        return len(self._state)

    def forget(self, watch_id: str) -> None:
        self._state.pop(watch_id, None)

    def check(self, watch_id: str, direction: str, structural_level: float,
              window_open_ts: int, start_ts: float, slot: Optional[int] = None,
              origin_ts: Optional[int] = None) -> Optional[dict]:
        """
        window_open_ts: open of the M15 candle this watch belongs to.
        start_ts: when the M5 confirmation happened; M1 candles that
          closed before the minute containing it are ignored.

        Returns:
          None                               -- still watching
          {"cancelled": True, "reason": str} -- give up this attempt
          {"entry_ts", "entry_price",
           "confirmed_at_minute",
           "seconds_after_m5_2_open"}         -- fire (from entry_timing_c.py;
                                                 minute/seconds are relative
                                                 to the first watched minute)
        """
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
            return None  # first watched minute has not closed yet

        # Single DB read, not one per candle.
        by_ts = {c["ts"]: c for c in dao.read_closed_candles("1m", limit=400)}

        # Walk in order; stop at the first candle not in storage yet so the
        # "first qualifying close" rule never skips a minute.
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
            return None  # idempotent no-op: nothing new since last check
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
