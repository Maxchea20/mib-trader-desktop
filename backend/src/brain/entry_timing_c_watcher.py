"""Entry-Timing C live watcher. NEW module.

Tracks active theses on qualifying M5 slots (1 or 2 -- M5#3 is never
routed here, see scenario_live_bridge.py's CONFIG["enabled_m5_slots"])
and, on each call (driven by the existing 5-second evaluation loop),
checks whether any NEW, fully closed 1-minute candle has appeared since
the last check for that thesis's window. If so, hands exactly those
candles to the unmodified src/brain/entry_timing_c.py::
find_m5_2_intrabar_entry() and returns whatever it decides.

Same confirmation rule for every slot it's used on -- only the window
being watched changes (M5#1's own 5-minute window vs M5#2's), never the
logic. No per-slot tuning.

Guarantees, by construction:
  - Idempotent: re-checking the same already-seen candle is a cheap
    no-op (tracked per-thesis in `_checked_ts`).
  - Never fabricates a candle from live tick price. A 1-minute slot is
    only ever read from storage, and only once its own close time has
    actually passed.
  - Never falls back to A or B on missing data. A candle whose time has
    passed but which is not found in storage is treated as a real sync
    gap -> CANCEL, with the reason stated.
  - One thesis can only ever be in-flight once: the caller
    (scenario_live_bridge.py) is responsible for the "one entry attempt
    per thesis" duplicate guard; this class only tracks per-thesis
    watch state, it does not itself decide whether a thesis is new.

This module does not import or modify scenario_engine.py. It knows
nothing about M15/M5 confluence -- it is handed direction, the
structural level, and the M5#2 candle's open timestamp by the caller,
which already determined those from the existing, unchanged engine.
"""
import json
import time
from typing import Dict, Optional

from ..market_data import data_access as dao
from ..market_data import database as db
from .entry_timing_c import find_m5_2_intrabar_entry

# A bit more than the 5-minute M5#2 window itself, to allow for the
# ~15-second candle-sync cadence before giving up and cancelling.
MAX_WAIT_SECONDS = 6 * 60


class EntryTimingCWatcher:
    def __init__(self):
        # thesis_id -> {"checked_ts": set(...), "started_at": float}
        self._state: Dict[str, dict] = {}
        self._restore_from_db()

    def _restore_from_db(self) -> None:
        """Restart safety: reload any thesis still marked 'watching' in
        the persistent store (scenario_c_watch table -- the existing
        SQLite DB, no second persistence system). A row with any other
        status (fired/cancelled) is a completed record, not resumed --
        it exists only so scenario_live_bridge's duplicate guard can see
        it was already handled. If scenario_engine.py's own in-memory
        Thesis for that origin_ts no longer exists after the restart
        (it never persists across a restart -- unrelated to this file,
        see report), this thesis's watch effectively cannot be driven
        forward again since nothing will call check() for it. Restoring
        it here is a best effort for the case where it can (a fast
        in-process restart, or a future engine-side persistence layer);
        it never causes incorrect re-firing either way, since check()
        still requires being called with real values by the caller."""
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
            # Restart safety must never crash startup. Worst case: an
            # in-flight watch is not resumed and simply won't be driven
            # forward (no incorrect re-fire results either way).
            pass

    def _persist(self, thesis_id: str, direction: str = None, origin_ts: int = None,
                 origin_level: float = None, status: str = "watching",
                 entry_ts: int = None, entry_price: float = None, reason: str = None) -> None:
        state = self._state.get(thesis_id, {})
        checked = sorted(state.get("checked_ts", set()))
        try:
            db.save_scenario_watch(
                thesis_id, direction=direction, origin_ts=origin_ts, origin_level=origin_level,
                status=status, checked_ts=json.dumps(checked),
                started_at=state.get("started_at"), entry_ts=entry_ts,
                entry_price=entry_price, reason=reason,
            )
        except Exception:
            pass  # persistence is a safety net, never a hard dependency for a live tick

    def active_count(self) -> int:
        return len(self._state)

    def forget(self, thesis_id: str) -> None:
        """Explicit cleanup hook (e.g. on thesis invalidation elsewhere)."""
        self._state.pop(thesis_id, None)

    def check(self, thesis_id: str, direction: str, structural_level: float,
              origin_ts: int, slot: int) -> Optional[dict]:
        """
        slot: which M5 candle of the thesis's life is being watched (1 or
          2 -- M5#3 is never routed here, see scenario_live_bridge.py).
          Determines ONLY which 5-minute window's own M1 candles get
          checked (window_open_ts = origin_ts + (slot-1)*300); the
          confirmation rule itself is identical regardless of slot --
          same unmodified entry_timing_c.find_m5_2_intrabar_entry() call,
          same "first qualifying M1 close" logic, no per-slot tuning.

        Returns:
          None                                -- still waiting, nothing new
          {"cancelled": True, "reason": str}   -- give up, do not enter
          {"entry_ts", "entry_price",
           "confirmed_at_minute",
           "seconds_after_m5_2_open"}          -- fire (from entry_timing_c.py, unmodified;
                                                    field name is unchanged even for M5#1 --
                                                    that file was not touched)
        """
        window_open_ts = origin_ts + (slot - 1) * 300
        is_new = thesis_id not in self._state
        state = self._state.setdefault(thesis_id, {"checked_ts": set(), "started_at": time.time()})
        if is_new:
            self._persist(thesis_id, direction=direction, origin_ts=origin_ts,
                           origin_level=structural_level, status="watching",
                           reason=f"watch started (M5#{slot})")

        if time.time() - state["started_at"] > MAX_WAIT_SECONDS:
            self.forget(thesis_id)
            reason = f"M5#{slot} window exceeded max wait with no qualifying M1 confirmation"
            self._persist(thesis_id, direction=direction, origin_ts=origin_ts,
                           origin_level=structural_level, status="cancelled", reason=reason)
            return {"cancelled": True, "reason": reason}

        window_ts = [window_open_ts + i * 60 for i in range(5)]
        now = time.time()
        # Only consider minutes whose close time has actually passed --
        # never look at a still-forming candle.
        due_ts = [t for t in window_ts if t + 60 <= now]
        if not due_ts:
            return None  # nothing in this window has even closed yet

        # Single DB read, not one per candle.
        recent = dao.read_closed_candles("1m", limit=400)
        by_ts = {c["ts"]: c for c in recent}

        candles = []
        for t in due_ts:
            row = by_ts.get(t)
            if row is None:
                # Expected candle's time has passed but it isn't in
                # storage -- a genuine sync gap, not "still forming".
                self.forget(thesis_id)
                reason = (f"M1 candle at ts={t} expected (past its close time) "
                          f"but not found in storage -- sync gap")
                self._persist(thesis_id, direction=direction, origin_ts=origin_ts,
                               origin_level=structural_level, status="cancelled", reason=reason)
                return {"cancelled": True, "reason": reason}
            candles.append(row)

        new_ts = {c["ts"] for c in candles if c["ts"] not in state["checked_ts"]}
        if not new_ts:
            return None  # idempotent no-op: nothing new since last check

        for t in new_ts:
            state["checked_ts"].add(t)
        self._persist(thesis_id, direction=direction, origin_ts=origin_ts,
                       origin_level=structural_level, status="watching",
                       reason=f"checked {len(state['checked_ts'])}/5 M1 candles")

        result = find_m5_2_intrabar_entry(direction, structural_level, window_open_ts, candles)
        if result is not None:
            self.forget(thesis_id)
            self._persist(thesis_id, direction=direction, origin_ts=origin_ts,
                           origin_level=structural_level, status="fired",
                           entry_ts=result["entry_ts"], entry_price=result["entry_price"],
                           reason=f"confirmed at minute {result['confirmed_at_minute']}")
            return result

        if len(candles) >= 5:
            # All 5 minutes of this window checked and closed; none
            # qualified. Per the mathematical identity discussed in
            # research (a genuinely eligible thesis's 5m close must equal
            # its last 1m candle's close), this should not occur for a
            # truly eligible thesis -- handled explicitly rather than
            # assumed.
            self.forget(thesis_id)
            reason = f"all 5 M1 candles of M5#{slot} checked, none confirmed the structural cross"
            self._persist(thesis_id, direction=direction, origin_ts=origin_ts,
                           origin_level=structural_level, status="cancelled", reason=reason)
            return {"cancelled": True, "reason": reason}

        return None