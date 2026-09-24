"""Scenario Engine — isolated, A/B-testable Hunt Brain redesign.

NOT wired into autotrader_loop.py or any live execution path. This module
is a pure decision engine: given closed 15m candles, closed 5m candles,
and the forming/live 5m bar, it returns a scenario classification, an
action (FIRE/WAIT/CANCEL), a human-readable explanation, and a technical
debug payload. No side effects, no order placement, no DB writes.

Three concepts are deliberately kept separate (2026-09-22 revision, per
explicit design request) because collapsing them is what produced the
"distance recovery == pullback" bug the first backtest caught:

  THESIS — the standing structural idea. "M15 broke bullish and the
    bullish idea remains valid." Persists across many ticks and can
    outlive more than one FIRE, until something actually invalidates it.

  SCENARIO — what the CURRENT tick looks like given that thesis. "Extended
    breakout, waiting for a pullback." Changes freely, tick to tick, as
    price/structure/context change. Never itself a verdict.

  EXECUTION EVENT — a specific, identifiable M5 structural confirmation
    (a rising edge from unconfirmed -> confirmed). Each one has an id.
    An event can be "spent" (already used to either FIRE or to register
    an EXTENDED_BREAKOUT) without invalidating the Thesis — a genuinely
    NEW event (a fresh rising edge, after confirmation was lost and
    regained, or after a real pullback) gets a new id and can fire
    again. A stale/reused event id can never fire twice.

Key correction from the first pass: an EXTENDED thesis can only reach
PULLBACK_WATCH via an ACTUAL, MEASURED price retracement toward the
origin level (tracked against price extremes and an ATR value FROZEN at
the moment extension began), never merely because live ATR/momentum
recalculates the extension threshold as smaller. See _update_pullback().

Locked parameters (2026-09-22):
  FAST_M5_PIVOT = 3, fixed, independent of the slow map engine's
    time-scaled pivot_window_for_timeframe().
  EXECUTABLE_DISTANCE_ATR ~= 1.0, adjusted by momentum context.
  PULLBACK_ZONE_ATR_MIN/MAX = 0.25 / 0.50, used both as the "how far
    back is a real retracement" test and as the "how close to the
    origin/S-R/FVG area counts as the pullback zone" test.
  Invalidation: close through invalidation_level OR a strong opposing
    fast-M5 CHoCH — either alone is sufficient.
  No candle-counting anywhere in this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT, NEUTRAL
from ..indicators import arrays, atr as _atr, find_pivots
from ..structure.observe import observe as obs_structure
from ..momentum.observe import observe as obs_momentum
from ..volume.observe import observe as obs_volume
from ..support_resistance.observe import observe as obs_sr
from ..fair_value_gap.observe import observe as obs_fvg
from .observation_hunt_c_fi import _parent_swings

# --------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------

FAST_M5_PIVOT = 3

EXECUTABLE_DISTANCE_ATR = 1.0
EXECUTABLE_DISTANCE_ATR_ACCEL_MULT = 1.3
EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT = 0.6

PULLBACK_ZONE_ATR_MIN = 0.25   # minimum genuine retracement to count as an actual pullback
PULLBACK_ZONE_ATR_MAX = 0.50   # width of the "acceptable entry area" band around the origin

NEARBY_SR_ATR = 0.30
NEARBY_FVG_ATR = 0.30

INVALIDATION_PIVOT = 2
OPPOSING_CHOCH_PIVOT = FAST_M5_PIVOT

SCENARIO_PRIORITY = [
    "SETUP_INVALIDATED",
    "REJECTION",
    "FRESH_PULLBACK_CONTINUATION",
    "FRESH_CLEAN_BREAKOUT",
    "EXTENDED_BREAKOUT",
    "PULLBACK_WATCH",
    "WEAK_BREAKOUT",
    "MOMENTUM_EXHAUSTION",
    "NO_SETUP",
]

ACTION_FOR_SCENARIO = {
    "SETUP_INVALIDATED": "CANCEL",
    "REJECTION": "CANCEL",
    "FRESH_PULLBACK_CONTINUATION": "FIRE",
    "FRESH_CLEAN_BREAKOUT": "FIRE",
    "EXTENDED_BREAKOUT": "WAIT",
    "PULLBACK_WATCH": "WAIT",
    "WEAK_BREAKOUT": "WAIT",
    "MOMENTUM_EXHAUSTION": "WAIT",
    "NO_SETUP": "WAIT",
}

_EXPLAIN = {
    "SETUP_INVALIDATED": (
        "The pullback broke the structure that supported the setup.",
        ["The original breakout thesis is no longer valid."],
    ),
    "REJECTION": (
        "Price reversed hard against the breakout direction on the 5-minute chart.",
        ["The breakout failed to hold.", "Standing down on this thesis."],
    ),
    "FRESH_PULLBACK_CONTINUATION": (
        "Price pulled back after the breakout, held the structure, and 5M is turning back in the breakout direction.",
        ["Pullback held the structural area.", "A fresh 5M confirmation just printed.", "Price is in an acceptable entry area."],
    ),
    "FRESH_CLEAN_BREAKOUT": (
        "15M structure broke, and 5M is confirming the same move right now.",
        ["15M and 5M structure agree.", "Price has not moved too far to enter."],
    ),
    "EXTENDED_BREAKOUT": (
        "The breakout is confirmed, but price has already moved too far, too quickly.",
        ["Do not chase.", "Waiting for an actual pullback, not just a smaller distance reading."],
    ),
    "PULLBACK_WATCH": (
        "Price genuinely pulled back toward the breakout area after an extended move.",
        ["Watching for a fresh 5M confirmation from here.", "The original 5M confirmation is already spent — it will not re-fire on its own."],
    ),
    "WEAK_BREAKOUT": (
        "15M produced a break, but it was a weak one and 5M hasn't confirmed yet.",
        ["Waiting to see if the move develops real follow-through."],
    ),
    "MOMENTUM_EXHAUSTION": (
        "The setup is still technically valid, but momentum is fading right where we'd want to enter.",
        ["Momentum is weakening.", "Skipping this execution window."],
    ),
    "NO_SETUP": (
        "No qualifying 15M structural break right now.",
        ["Nothing to hunt.", "Sitting out."],
    ),
}


@dataclass
class Thesis:
    """The standing structural idea. Facts only — see module docstring.

    2026-09-22 diagnostics revision: adds thesis_id and a set of "last_*"
    / *_count fields purely for observability. NONE of these fields are
    read by any decision condition anywhere in this file — grep for
    "thesis.last_" or "thesis.fire_count" etc. and you will only find
    them written (here and in tick()) and read back out into the debug
    payload. The engine's behavior (what fires, when, on which event id)
    is governed exclusively by status / m5_event_id / consumed_m5_event_id
    / extension_price / extension_atr_ref / pullback_confirmed, all
    unchanged from the previous revision.
    """
    direction: str
    origin_event: str
    origin_level: float
    origin_ts: int
    invalidation_level: Optional[float]
    status: str = "ARMED"  # ARMED | EXTENDED | PULLBACK_WATCH | INVALIDATED
    thesis_id: str = ""

    # --- M5 execution-event tracking (rising-edge based; DECISION STATE) ---
    m5_event_id: int = 0
    m5_was_confirmed: bool = False
    consumed_m5_event_id: int = 0  # last event id already spent (FIRE or EXTENDED registration)

    # --- actual-pullback tracking (price movement, not distance recovery; DECISION STATE) ---
    extension_price: Optional[float] = None
    extension_atr_ref: Optional[float] = None
    pullback_confirmed: bool = False

    # --- DIAGNOSTIC-ONLY history, never reset, never read by the classifier ---
    pullback_count: int = 0        # how many distinct pullback episodes this thesis has had
    fire_count: int = 0            # how many FIREs this thesis has produced
    last_extension_price: Optional[float] = None
    last_extension_atr_ref: Optional[float] = None
    last_pullback_price: Optional[float] = None          # price at the moment pullback_confirmed flipped on
    last_pullback_retracement: Optional[float] = None    # raw retrace_raw measured at that moment
    last_pullback_required_retracement: Optional[float] = None  # the threshold it had to clear
    last_pullback_ts: Optional[int] = None
    last_fire_event_id: Optional[int] = None
    last_fire_scenario: Optional[str] = None
    last_fire_ts: Optional[int] = None


@dataclass
class TraceEvent:
    ts: int
    text: str


# --------------------------------------------------------------------
# M15 thesis candidate
# --------------------------------------------------------------------

def _m15_candidate(candles_15m: List[dict]) -> Optional[Dict[str, Any]]:
    if not candles_15m or len(candles_15m) < 60:
        return None
    st = obs_structure(candles_15m, "15m")
    bar_ts = int(candles_15m[-1]["ts"])
    fresh = [e for e in (st.history or []) if e.timestamp == bar_ts]
    candidate = None
    for e in fresh:
        if e.event_type == "CHoCH":
            candidate = e
        elif e.event_type == "BOS" and not st.flags.get("extended_bos"):
            candidate = candidate or e
    if candidate is None:
        return None
    direction = LONG if candidate.direction == "BULLISH" else (SHORT if candidate.direction == "BEARISH" else None)
    if direction is None:
        return None
    hl, lh = _parent_swings(candles_15m, INVALIDATION_PIVOT)
    invalidation_level = hl if direction == LONG else lh
    return {
        "direction": direction,
        "event": candidate.event_type,
        "level": float(candidate.reference_price or candidate.price),
        "ts": bar_ts,
        "invalidation_level": invalidation_level,
    }


# --------------------------------------------------------------------
# Fast M5 execution pivot
# --------------------------------------------------------------------

def _fast_pivots_5m(closed_5m: List[dict]) -> Dict[str, Optional[float]]:
    if not closed_5m or len(closed_5m) < (2 * FAST_M5_PIVOT + 1):
        return {"high": None, "low": None}
    a = arrays(closed_5m)
    piv = find_pivots(a["high"], a["low"], left=FAST_M5_PIVOT, right=FAST_M5_PIVOT)
    highs = [p for p in piv if p["type"] == "H"]
    lows = [p for p in piv if p["type"] == "L"]
    return {
        "high": float(highs[-1]["price"]) if highs else None,
        "low": float(lows[-1]["price"]) if lows else None,
    }


def _m5_confirmation(direction: str, level: Optional[float], forming_5m: Optional[dict],
                      closed_5m: List[dict]) -> Dict[str, Any]:
    out = {"level": level, "live": False, "closed": False, "live_price": None}
    if level is None:
        return out
    if forming_5m is not None:
        price = float(forming_5m["close"])
        out["live_price"] = price
        out["live"] = (direction == LONG and price > level) or (direction == SHORT and price < level)
    if closed_5m:
        last = closed_5m[-1]
        c = float(last["close"])
        out["closed"] = (direction == LONG and c > level) or (direction == SHORT and c < level)
    return out


def _opposing_fast_choch(direction: str, closed_5m: List[dict]) -> bool:
    if not closed_5m or len(closed_5m) < 60:
        return False
    st = obs_structure(closed_5m, "5m", pivot_window_override=OPPOSING_CHOCH_PIVOT)
    bar_ts = int(closed_5m[-1]["ts"])
    for e in (st.history or []):
        if e.timestamp != bar_ts or e.event_type != "CHoCH":
            continue
        opp = SHORT if direction == LONG else LONG
        if (e.direction == "BULLISH" and opp == LONG) or (e.direction == "BEARISH" and opp == SHORT):
            return True
    return False


# --------------------------------------------------------------------
# Execution-quality read
# --------------------------------------------------------------------

def _execution_quality(direction: str, origin_level: float, price: float, atr15: float,
                        mom_obs, vol_obs, sr_obs) -> Dict[str, Any]:
    distance_atr = abs(price - origin_level) / atr15 if atr15 else 0.0
    tags = set(mom_obs.tags or [])
    momentum_state = "EXHAUSTING" if "EXHAUSTING" in tags else ("ACCELERATING" if "ACCELERATING" in tags else "NEUTRAL")
    threshold = EXECUTABLE_DISTANCE_ATR
    if momentum_state == "ACCELERATING":
        threshold *= EXECUTABLE_DISTANCE_ATR_ACCEL_MULT
    elif momentum_state == "EXHAUSTING":
        threshold *= EXECUTABLE_DISTANCE_ATR_EXHAUST_MULT
    room_atr = sr_obs.measurement("distance_to_resistance_atr") if direction == LONG \
        else sr_obs.measurement("distance_to_support_atr")
    blocked_by_sr = room_atr is not None and room_atr <= NEARBY_SR_ATR
    volume_tags = set(vol_obs.tags or [])
    volume_state = "ABSORPTION" if any("ABSORPTION" in t for t in volume_tags) else \
        ("EXHAUSTION" if any("EXHAUSTION" in t for t in volume_tags) else "NEUTRAL")
    executable = (distance_atr <= threshold) and not blocked_by_sr and volume_state != "ABSORPTION"
    return {
        "distance_atr": round(distance_atr, 3),
        "threshold_atr": round(threshold, 3),
        "momentum_state": momentum_state,
        "volume_state": volume_state,
        "room_to_structure_atr": round(room_atr, 3) if room_atr is not None else None,
        "blocked_by_sr": blocked_by_sr,
        "executable": executable,
    }


def _in_pullback_zone(direction: str, origin_level: float, price: float, atr15: float,
                       sr_obs, fvg_obs) -> bool:
    """Is CURRENT PRICE LOCATED in an acceptable re-entry area? This is a
    LOCATION test only. It does NOT by itself prove an actual pullback
    happened — see _update_pullback(), which also requires measured
    retracement from the extension extreme before pullback_confirmed
    is set."""
    if atr15 <= 0:
        return False
    near_origin = abs(price - origin_level) <= PULLBACK_ZONE_ATR_MAX * atr15
    room_atr = sr_obs.measurement("distance_to_resistance_atr") if direction == SHORT \
        else sr_obs.measurement("distance_to_support_atr")
    near_sr = room_atr is not None and room_atr <= NEARBY_SR_ATR
    near_fvg = False
    for lv in (fvg_obs.levels or []):
        if lv.price is not None and abs(lv.price - price) <= NEARBY_FVG_ATR * atr15:
            near_fvg = True
            break
    return near_origin or near_sr or near_fvg


def _update_pullback(thesis: Thesis, direction: str, price: float, atr15: float,
                      sr_obs, fvg_obs) -> bool:
    """Track extension extremes and decide whether an ACTUAL pullback has
    now occurred. Returns True the tick pullback_confirmed newly flips on.

    Deliberately decoupled from the live execution-quality threshold:
    retracement is measured in raw price against extension_atr_ref, which
    is FROZEN at the moment the thesis first became EXTENDED. A later
    change in live ATR or momentum cannot, by itself, satisfy this test —
    only price actually moving back toward the origin level can.
    """
    if thesis.extension_atr_ref is None:
        thesis.extension_atr_ref = atr15 if atr15 > 0 else None
    if thesis.extension_price is None:
        thesis.extension_price = price
    else:
        if direction == LONG:
            thesis.extension_price = max(thesis.extension_price, price)
        else:
            thesis.extension_price = min(thesis.extension_price, price)

    # DIAGNOSTIC ONLY: mirror the live tracking fields into "last_*" so a
    # later FIRE reset (which clears extension_price/extension_atr_ref for
    # the NEXT episode's decision logic) doesn't erase the historical
    # record of what this episode's numbers actually were. Nothing here
    # feeds back into the pullback decision below.
    thesis.last_extension_price = thesis.extension_price
    thesis.last_extension_atr_ref = thesis.extension_atr_ref

    if thesis.pullback_confirmed or not thesis.extension_atr_ref:
        return False

    retrace_raw = (thesis.extension_price - price) if direction == LONG else (price - thesis.extension_price)
    required_retrace = PULLBACK_ZONE_ATR_MIN * thesis.extension_atr_ref
    retraced_enough = retrace_raw >= required_retrace
    located_in_zone = _in_pullback_zone(direction, thesis.origin_level, price, atr15, sr_obs, fvg_obs)

    if retraced_enough and located_in_zone:
        thesis.pullback_confirmed = True
        # DIAGNOSTIC ONLY — decision already made above; this just records it.
        thesis.pullback_count += 1
        thesis.last_pullback_price = price
        thesis.last_pullback_retracement = retrace_raw
        thesis.last_pullback_required_retracement = required_retrace
        return True
    return False


def _invalidated(thesis: Thesis, price: float, closed_5m: List[dict]) -> Optional[str]:
    if thesis.invalidation_level is not None:
        if thesis.direction == LONG and price < thesis.invalidation_level:
            return "close_through_invalidation_level"
        if thesis.direction == SHORT and price > thesis.invalidation_level:
            return "close_through_invalidation_level"
    if _opposing_fast_choch(thesis.direction, closed_5m):
        return "opposing_fast_choch"
    return None


# --------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------

class ScenarioEngine:
    def __init__(self):
        self.thesis: Optional[Thesis] = None
        self.trace: List[TraceEvent] = []
        self._last_scenario: Optional[str] = None
        self._last_action: Optional[str] = None
        self._last_m15_ts_used: Optional[int] = None
        self._thesis_counter: int = 0  # diagnostic-only, feeds thesis_id labels

    def _log(self, ts: int, text: str) -> None:
        self.trace.append(TraceEvent(ts=ts, text=text))

    def tick(self, candles_15m: List[dict], closed_5m: List[dict],
              forming_5m: Optional[dict] = None) -> Dict[str, Any]:
        ts = int((forming_5m or closed_5m[-1] if closed_5m else candles_15m[-1])["ts"])
        price = float((forming_5m or closed_5m[-1])["close"]) if (forming_5m or closed_5m) else None
        aa15 = arrays(candles_15m)
        atr15 = float(_atr(aa15["high"], aa15["low"], aa15["close"], 14) or 0.0)

        # -- thesis creation / invalidation -----------------------------
        if self.thesis is not None:
            reason = _invalidated(self.thesis, price, closed_5m) if price is not None else None
            if reason:
                self.thesis.status = "INVALIDATED"
                self._log(ts, f"Thesis invalidated ({reason}).")
        else:
            cand = _m15_candidate(candles_15m)
            if cand and cand["ts"] != self._last_m15_ts_used:
                self._thesis_counter += 1
                # BUG FIX: thesis_id used to be purely a per-process
                # counter (TH-000001, TH-000002, ...), which resets to
                # zero on every restart -- meaning a brand-new thesis
                # after a restart could get the exact same ID as some
                # completely different thesis from a prior run.
                # scenario_live_bridge.py's duplicate-attempt guard is
                # restored from persisted history across restarts and
                # keys on this ID alone, so a collision meant a genuine
                # new opportunity could be silently treated as already
                # handled. Confirmed directly: two fresh instances
                # produce an identical first ID otherwise. cand["ts"]
                # (the M15 origin candle's own timestamp) is already
                # guaranteed unique per genuine thesis by construction --
                # a new thesis is only ever created when no thesis is
                # currently active AND a fresh M15 event just occurred --
                # so basing the ID on it fixes the collision permanently,
                # with no counter needed at all. Format kept recognizable.
                self.thesis = Thesis(
                    direction=cand["direction"], origin_event=cand["event"],
                    origin_level=cand["level"], origin_ts=cand["ts"],
                    invalidation_level=cand["invalidation_level"],
                    thesis_id=f"TH-{cand['ts']}",
                )
                self._last_m15_ts_used = cand["ts"]
                self._log(ts, f"THESIS OPENED [{self.thesis.thesis_id}]: 15M {cand['event']} "
                              f"confirmed ({cand['direction']}).")

        thesis = self.thesis
        m5 = {"level": None, "live": False, "closed": False, "live_price": price}
        exq = None
        weak_breakout = False
        just_pulled_back = False
        fresh_event = False
        confirmed_now = False

        if thesis is not None and thesis.status != "INVALIDATED" and price is not None:
            fast = _fast_pivots_5m(closed_5m)
            level = fast["high"] if thesis.direction == LONG else fast["low"]
            m5 = _m5_confirmation(thesis.direction, level, forming_5m, closed_5m)
            confirmed_now = bool(m5["live"] or m5["closed"])

            # -- rising-edge M5 EXECUTION EVENT tracking --------------
            if confirmed_now and not thesis.m5_was_confirmed:
                thesis.m5_event_id += 1
                self._log(ts, f"EXECUTION EVENT [{thesis.thesis_id} / M5-E{thesis.m5_event_id:03d}]: "
                              f"fresh 5M {'bullish' if thesis.direction == LONG else 'bearish'} confirmation.")
            elif (not confirmed_now) and thesis.m5_was_confirmed:
                self._log(ts, f"[{thesis.thesis_id}] Live 5M confirmation lost (price reversed before it could fire).")
            thesis.m5_was_confirmed = confirmed_now
            fresh_event = confirmed_now and thesis.m5_event_id != thesis.consumed_m5_event_id

            mom = obs_momentum(closed_5m, "5m") if len(closed_5m) >= 30 else None
            vol = obs_volume(closed_5m, "5m") if len(closed_5m) >= 30 else None
            sr = obs_sr(candles_15m, "15m") if len(candles_15m) >= 30 else None
            fvg = obs_fvg(candles_15m, "15m") if len(candles_15m) >= 30 else None

            if mom and vol and sr:
                exq = _execution_quality(thesis.direction, thesis.origin_level, price, atr15, mom, vol, sr)
            if sr is not None:
                br = obs_structure(candles_15m, "15m")
                bd = br.measurement("break_distance_atr", 0.0)
                weak_breakout = bool(bd is not None and bd < 0.15)

            # -- actual-pullback tracking (only relevant once EXTENDED) --
            if thesis.status in ("EXTENDED", "PULLBACK_WATCH") and sr is not None and fvg is not None:
                just_pulled_back = _update_pullback(thesis, thesis.direction, price, atr15, sr, fvg)
                if just_pulled_back:
                    thesis.last_pullback_ts = ts  # diagnostic only
                    self._log(ts, f"ACTUAL PULLBACK confirmed [{thesis.thesis_id} / PB-{thesis.pullback_count:03d}]: "
                                  f"retraced {thesis.last_pullback_retracement:.1f} "
                                  f"(required >= {thesis.last_pullback_required_retracement:.1f}) "
                                  f"toward {thesis.origin_level:.1f} from extension extreme "
                                  f"{thesis.last_extension_price:.1f}.")

        # -- scenario classification (explicit priority) -----------------
        scenario = "NO_SETUP"
        if thesis is not None and thesis.status == "INVALIDATED":
            # distinguish a hard structural break (SETUP_INVALIDATED) from an
            # opposing-CHoCH rejection: both set status INVALIDATED above,
            # but we re-derive which reason applied for the label.
            reason = _invalidated(thesis, price, closed_5m) if price is not None else None
            scenario = "REJECTION" if reason == "opposing_fast_choch" else "SETUP_INVALIDATED"
        elif thesis is not None and thesis.status == "PULLBACK_WATCH" and fresh_event:
            scenario = "FRESH_PULLBACK_CONTINUATION"
        elif thesis is not None and thesis.status == "PULLBACK_WATCH":
            scenario = "PULLBACK_WATCH"
        elif thesis is not None and thesis.status == "EXTENDED":
            # distance/ATR/momentum recovering is NOT enough — only an
            # actually-measured retracement (thesis.pullback_confirmed,
            # set by _update_pullback against a FROZEN extension_atr_ref)
            # may promote this to PULLBACK_WATCH.
            scenario = "PULLBACK_WATCH" if thesis.pullback_confirmed else "EXTENDED_BREAKOUT"
        elif thesis is not None and thesis.status == "ARMED" and fresh_event and exq is not None:
            scenario = "FRESH_CLEAN_BREAKOUT" if exq["executable"] else "EXTENDED_BREAKOUT"
        elif thesis is not None and weak_breakout:
            scenario = "WEAK_BREAKOUT"
        elif thesis is not None and exq is not None and not exq["executable"] and exq["momentum_state"] == "EXHAUSTING":
            scenario = "MOMENTUM_EXHAUSTION"
        elif thesis is not None:
            scenario = "WEAK_BREAKOUT"

        # promote status from ARMED->EXTENDED / EXTENDED->PULLBACK_WATCH,
        # spend the m5 event id whenever it is used for a non-fire verdict
        if thesis is not None and thesis.status != "INVALIDATED":
            if scenario == "EXTENDED_BREAKOUT" and thesis.status == "ARMED":
                thesis.status = "EXTENDED"
                thesis.consumed_m5_event_id = thesis.m5_event_id
            elif scenario == "PULLBACK_WATCH" and thesis.status == "EXTENDED":
                thesis.status = "PULLBACK_WATCH"

        action = ACTION_FOR_SCENARIO[scenario]

        # -- FIRE: spend the event id, reset for the NEXT execution episode
        # within the SAME thesis (thesis itself is not discarded — only
        # invalidation discards it) --------------------------------------
        fire_id = None
        if action == "FIRE" and thesis is not None:
            # DIAGNOSTIC ONLY, recorded before the reset below so the
            # numbers that led to this fire aren't lost:
            thesis.fire_count += 1
            thesis.last_fire_event_id = thesis.m5_event_id
            thesis.last_fire_scenario = scenario
            thesis.last_fire_ts = ts
            fire_id = f"{thesis.thesis_id}/FIRE-{thesis.fire_count:03d}"

            # DECISION STATE reset — unchanged from the previous revision:
            thesis.consumed_m5_event_id = thesis.m5_event_id
            thesis.status = "ARMED"
            thesis.extension_price = None
            thesis.extension_atr_ref = None
            thesis.pullback_confirmed = False

        if scenario != self._last_scenario:
            self._log(ts, f"SCENARIO changed to {scenario}.")
        if action != self._last_action:
            self._log(ts, f"ACTION changed to {action}.")
        self._last_scenario, self._last_action = scenario, action

        what_happening, why = _EXPLAIN[scenario]
        thesis_summary = (
            f"M15 broke {thesis.direction.lower()} ({thesis.origin_event}) and the "
            f"{'bullish' if thesis.direction == LONG else 'bearish'} structural idea remains valid."
            if thesis is not None else "No active structural idea."
        )
        execution_event_text = (
            f"Fresh M5 {'bullish' if thesis and thesis.direction == LONG else 'bearish'} confirmation "
            f"(event #{thesis.m5_event_id})." if thesis and fresh_event else
            ("5M confirmation already used for this episode — needs a new one." if thesis and confirmed_now else
             "No new M5 event yet.")
        )

        result = {
            "ts": ts,
            "direction": thesis.direction if thesis else NEUTRAL,
            "thesis_id": thesis.thesis_id if thesis else None,
            "thesis": thesis_summary,
            "scenario": scenario,
            "execution_event": execution_event_text,
            "action": action,
            "entry": price if action == "FIRE" else None,
            "fire_id": fire_id,
            "what_happening": what_happening,
            "why": why,
            "debug": {
                "thesis": None if thesis is None else {
                    "thesis_id": thesis.thesis_id,
                    "direction": thesis.direction, "origin_event": thesis.origin_event,
                    "origin_level": thesis.origin_level, "origin_ts": thesis.origin_ts,
                    "invalidation_level": thesis.invalidation_level, "status": thesis.status,
                    # decision state (live — reset after each FIRE, see module docstring)
                    "m5_event_id": thesis.m5_event_id, "consumed_m5_event_id": thesis.consumed_m5_event_id,
                    "extension_price": thesis.extension_price, "extension_atr_ref": thesis.extension_atr_ref,
                    "pullback_confirmed": thesis.pullback_confirmed,
                    # preserved diagnostic history (NOT reset, NOT read by the classifier)
                    "pullback_count": thesis.pullback_count,
                    "fire_count": thesis.fire_count,
                    "last_extension_price": thesis.last_extension_price,
                    "last_extension_atr_ref": thesis.last_extension_atr_ref,
                    "last_pullback_price": thesis.last_pullback_price,
                    "last_pullback_retracement": thesis.last_pullback_retracement,
                    "last_pullback_required_retracement": thesis.last_pullback_required_retracement,
                    "last_pullback_ts": thesis.last_pullback_ts,
                    "last_fire_event_id": thesis.last_fire_event_id,
                    "last_fire_scenario": thesis.last_fire_scenario,
                    "last_fire_ts": thesis.last_fire_ts,
                },
                "m5": m5,
                "execution_quality": exq,
                "atr15": atr15,
                "fresh_event": fresh_event,
                "just_pulled_back": just_pulled_back,
                "weak_breakout": weak_breakout,
                "fast_m5_pivot": FAST_M5_PIVOT,
            },
        }

        if thesis is not None and thesis.status == "INVALIDATED":
            self.thesis = None

        return result