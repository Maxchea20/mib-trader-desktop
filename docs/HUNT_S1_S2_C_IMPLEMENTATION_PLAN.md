# HUNT + S1/S2 + C — LOCKED IMPLEMENTATION PLAN

Saved 2026-09-24. **Do not code until this plan is approved.**

This file is the implementation source of truth for the rebuild.
Live Isolated today is still Hunt C-FI only (no S1/S2/C).

Do not restore `scenario_engine`, `scenario_live_bridge`, or `entry_engine=scenario`.

---

## Locked decisions (2026-09-24)

1. 5m BOS/CHoCH via existing structure engine with **`pivot_window_override=2`**. Not default W=15. Not `FAST_M5_PIVOT=3`.
2. **C `structural_level` = M5 event `reference_price`**, else `price`, else no C / no FIRE. Hunt 15m origin_level is **only** for S2 extension / pullback / origin-zone.
3. **Suppress Hunt V2 FIRE on slot 1/2.** Those slots only send after S1/S2 → C success. C miss does **not** fall back to V2. **Slot 3 impulse_3 unchanged, no C.**

---

## Architecture

```
HUNT M15 thesis
        ↓
ARMED
   ├── S1: forming 15m slot 1/2 → M5 BOS/CHoCH → C
   └── S2: extend → pullback → NEW M5 BOS/CHoCH (slot 1/2/3) → C
        ↓
C success → ONE Hunt FIRE → existing loop guards → _open_live_from_hunt

SEPARATE: Hunt slot-3 impulse_3 → FIRE → same guards → same pipe
```

Never: second engine, second pipe, S1 ticket + S2 ticket, C miss → Hunt FIRE, C after invalidation.

---

## A. Hunt thesis state (internal)

Public `hunt["action"]` stays `WAIT` | `FIRE`.

Internal `STATE["hunt_timing"]` or `hunt["timing"]`:

| State | Meaning |
|---|---|
| `WAIT` | No Hunt thesis |
| `ARMED` | Thesis valid; no C window |
| `S2_EXTENDED` | Extension M5 consumed; measuring pullback |
| `S2_PULLBACK` | Pullback confirmed; wait new M5 |
| `C_WATCH` | One C window |
| `FIRE` | C success **or** slot-3 impulse_3 |
| `INVALID` | Thesis dead; wipe timing |

Thesis source: existing `evaluate_hunt_c_fi` gates (cfast / internal / rearm). Weather still blocks **send** in `autotrader_loop`, not thesis birth.

**Slot 1/2 V2:** `evaluate_hunt_c_fi` must **not** return executable `FIRE` on V2 tap. Stamp thesis + slot only (`ARMED`). Slot 3 V3 / impulse_3 still returns `FIRE`.

---

## B. S1 transitions

```
ARMED + forming 15m
  + this 5m is slot 1 or 2 of that 15m
  + obs_structure(5m, "5m", pivot_window_override=2)
      fresh same-direction BOS or CHoCH
      timestamp == this 5m open
      event id not consumed
        → consume id
        → C_WATCH (path=S1, window=this 5m open,
                   level=reference_price or price)
C success → hunt action FIRE, timing=S1
C miss    → clear C_WATCH, back to ARMED
INVALID   → wipe
```

Slot 3 is never S1.

---

## C. S2 transitions

```
ARMED
  + same-direction M5 BOS/CHoCH (override=2)
  + NOT executable:
        distance_atr = |price - hunt_origin| / atr15
        threshold 1.0 × (1.3 accel / 0.6 exhaust)
        or SR room ≤ 0.30 ATR
        or volume ABSORPTION
        → consume that M5 id
        → S2_EXTENDED
        → freeze extension_atr_ref = atr15
        → extension_price = max/min price

S2_EXTENDED
  + retrace ≥ 0.25 × extension_atr_ref
  + location: 0.50 ATR of origin OR 0.30 SR OR 0.30 FVG
        (location width uses live atr15)
        → S2_PULLBACK

S2_PULLBACK
  + NEW M5 BOS/CHoCH same direction, new id
  + slot 1 or 2 or 3 of the relevant 15m
        → consume id
        → C_WATCH (path=S2, that new 5m,
                   level=that M5 reference_price or price)

C success → hunt action FIRE, timing=S2
C miss    → clear C_WATCH, ARMED (no V2, no impulse_3)
INVALID   → wipe
```

---

## D. M5 structure (existing functions only)

```
from src.structure.observe import observe as obs_structure

st = obs_structure(candles_5m, "5m", pivot_window_override=2)
```

From `st.history` (or current event):

- `event_type` in `BOS`, `CHoCH`, `CHOCH`
- `direction` → map to Hunt LONG/SHORT
- `timestamp`
- `reference_price` or `price`

Same engine as 15m. Override=2 only.

---

## E. Event IDs

```
key = (event_type, timestamp, round(level, 1), direction)
rising edge if key not seen
consume when starting C_WATCH or entering S2_EXTENDED
if key == consumed → ignore
```

---

## F. C state

Reuse `find_m5_2_intrabar_entry` in `entry_timing_c.py`. Do not restore the scenario watcher as an engine.

Fields: path, window_open_ts, structural_level, direction, checked 1m ts.

Candles: `window_open + 0..4 minutes`. CLOSE only. Incomplete 1m → no trigger.

LONG: close > level. SHORT: close < level. First hit wins.

---

## G. Levels

| Use | Field |
|---|---|
| S2 distance / pullback / origin zone | Hunt 15m origin / `thesis_level` |
| C | qualifying M5 `reference_price` else `price` |
| Missing both M5 prices | no C, no FIRE |

Do not convert Hunt origin into C level.

---

## H. Invalidation

Hunt thesis gone (existing C-FI drop: opposite CHoCH / extended 15m BOS / `thesis_invalid` close if wired):

```
INVALID → cancel S2_* and C_WATCH → no FIRE
```

C must not succeed on a dead thesis.

---

## I. S1 vs S2 (one path)

```
if C_WATCH: ignore new qualify
elif S1 qualifies this tick: take S1 (drop S2 measuring)
elif S2 fresh M5 qualifies: take S2
```

Same tick: S1 wins. One watcher. One ticket.

---

## J. C miss

Five 1m closes, no cross → cancel this attempt. No FIRE. No V2. No slot-3. Thesis may stay ARMED.

---

## K. Final FIRE

```
C success  OR  existing slot-3 impulse_3
        ↓
hunt["action"] = "FIRE"
hunt["timing"] = "S1" | "S2" | "SLOT3"
        ↓
autotrader_loop.evaluate
  weather, one Isolated, last_fired_5m_ts, cooldown, levels
        ↓
_open_live_from_hunt
```

---

## L. Files

**Add**

- `backend/src/brain/hunt_entry_timing.py`
  — state machine, S1/S2, event ids, C drive. No Isolated.

**Modify**

- `backend/src/brain/observation_hunt_c_fi.py`
  — suppress slot 1/2 V2 as FIRE; keep slot 3 FIRE; attach thesis/slot; call timing; stamp `timing`.
- `backend/src/analysis_observations.py`
  — pass 5m/1m (+ mom/vol/SR/FVG for S2) into timing if not already on the hunt call.
- `backend/src/autotrader_loop.py`
  — copy `timing` onto `last_hunt` only. **No second send.**

**Reuse**

- `backend/src/brain/entry_timing_c.py` → `find_m5_2_intrabar_entry` only.

**Untouched**

- `autotrader_exec.py`
- `weather.py`
- `lifecycle*`
- Hunt V3 / impulse_3 math
- MEXC private
- Frontend (optional later: show `timing` on the same Hunt FIRE)

**Do not restore**

- `scenario_engine.py`
- `scenario_live_bridge.py`
- `entry_engine=scenario`

---

## Functions to implement (names)

In `hunt_entry_timing.py`:

- `slot_of_5m(ts) -> 1|2|3`
- `m5_structure_events(candles_5m) -> list`  # observe override=2
- `fresh_m5_event(events, direction, last_5m_ts, consumed) -> event|None`
- `event_key(event) -> tuple`
- `s2_executable(price, origin, atr15, mom, vol, sr) -> bool`
- `s2_update_pullback(state, price, atr15, sr, fvg) -> bool`
- `start_c_watch(state, path, m5_ts, level, direction)`
- `tick_c(state, one_minute_candles) -> SUCCESS|MISS|NONE`
- `tick_timing(hunt_thesis, candles_15m, candles_5m, candles_1m, aux) -> hunt_patch`

In `evaluate_hunt_c_fi`:

- After current gates, if slot in (1, 2) and current action would be V2 FIRE → set action WAIT, keep thesis fields, let `tick_timing` promote to FIRE.
- If slot == 3 and V3 FIRE → leave as FIRE, `timing=SLOT3`, do not start C.

---

## Out of scope this patch

- C on slot 3
- S3
- Restoring the deleted scenario engine
- Changing SL/TP, sizing, Isolated
- New public probe fields beyond `timing` on last_hunt
