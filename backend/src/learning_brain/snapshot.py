"""Freeze Brain 1 state at T. Never recompute from later bars."""
from __future__ import annotations
import json, time, uuid
from typing import Dict, Optional
from . import features as featmod
from .isolation import fail_open
from .schema import conn, init
TIMING_VERSION = "HUNT_ENTRY_TIMING_S1S2C"

def _thesis_id(hunt):
    ts, side, lvl = hunt.get("thesis_ts"), hunt.get("direction"), hunt.get("thesis_level")
    if ts is None or not side: return None
    return f"{ts}|{side}|{lvl}"

def row_kind(hunt, last_action=None):
    action = str(hunt.get("action") or "WAIT").upper()
    phase = str(hunt.get("timing_state") or "")
    la = str(last_action or "")
    if action == "FIRE":
        if la.startswith("LIVE OPEN") or la.startswith("OPEN "): return "FIRE_SENT"
        if "WEATHER" in la: return "WEATHER_BLOCK"
        if "COOLDOWN" in la: return "COOLDOWN"
        if "NO LEVELS" in la: return "FIRE_NO_LEVELS"
        return "FIRE_CANDIDATE"
    if hunt.get("timing_miss"): return "C_MISS"
    if phase == "INVALID": return "INVALID"
    if phase: return phase
    return action or "WAIT"

@fail_open(None)
def record(hunt=None, weather=None, extra=None, last_action=None, source="live", trade_id=None, ts=None):
    init()
    hunt = hunt or {}; extra = extra or {}
    now = int(ts if ts is not None else time.time())
    kind = row_kind(hunt, last_action)
    if kind == "WAIT" and not hunt.get("thesis_ts") and not hunt.get("armed"):
        return None
    features = featmod.extract(hunt, weather, now, extra)
    bar15, bar5, bar1 = extra.get("bar_ts_15m"), extra.get("bar_ts_5m"), extra.get("bar_ts_1m")
    if bar15 and int(bar15) > now + 1: bar15 = None
    if bar5 and int(bar5) > now + 1: bar5 = None
    if bar1 and int(bar1) > now + 1: bar1 = None
    obs_id = f"LB-{uuid.uuid4().hex[:16]}"
    slim = {k: hunt.get(k) for k in ("action","direction","event","gate","timing","timing_state","thesis_ts","thesis_level","thesis_invalid","entry","stop","target","atr_15m","armed","slot","brain_version","timing_miss")}
    with conn() as c:
        c.execute("""INSERT INTO lb_snapshots (obs_id, thesis_id, hunt_version, timing_version, ts, bar_ts_15m, bar_ts_5m, bar_ts_1m, row_kind, source, action, direction, event, gate, timing, timing_state, slot, weather, features_json, hunt_json, label_status, trade_id) VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?)""",
            (obs_id, _thesis_id(hunt), hunt.get("brain_version") or "OBSERVATION_HUNT_M5_C_FI", TIMING_VERSION, now, bar15, bar5, bar1, kind, source, hunt.get("action"), hunt.get("direction"), hunt.get("event"), hunt.get("gate"), hunt.get("timing"), hunt.get("timing_state"), hunt.get("slot"), (weather or {}).get("flag"), featmod.dumps(features), json.dumps(slim, default=str), "OPEN", trade_id))
        c.commit()
    return obs_id
