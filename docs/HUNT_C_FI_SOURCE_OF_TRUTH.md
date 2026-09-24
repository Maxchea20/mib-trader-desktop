# HUNT C-FI — SOURCE OF TRUTH

Saved 2026-09-24.

Live Isolated engine as of this date. Version string: `OBSERVATION_HUNT_M5_C_FI`.

Do not mix S1/S2/C into this file. S1/S2 spec is `docs/S1_S2_SOURCE_OF_TRUTH.md`.

---

## What Hunt is

Hunt is the **live brain**. When it prints FIRE with entry / stop / target,
`autotrader_loop.evaluate()` sends Isolated via `_open_live_from_hunt`.

It is not S1. It is not S2. It does not use the 1m C watcher.

---

## Three gates (any one can arm)

**Gate A — C-fast**  
Swing 15m CHoCH, or a BOS that is not an extended BOS. Wider 15m pivots.

**Gate B — Internal**  
Same event types on tighter 15m pivots (L/R = 2). Fresh on the current 15m.

**Gate C — Rearm**  
Last valid 15m event is still in force (no opposite CHoCH), even if it
printed on an earlier 15m. 5m may answer again. This is why probe often
says: “15m thesis is still valid. Waiting for the 5-minute candle to answer again.”

3rd BOS in the same direction is dropped (extended). CHoCH resets that streak.

---

## How 5m “answers” (fill)

A 15m is three 5m slots:

```
15m candle
 ├── M5#1  slot 1
 ├── M5#2  slot 2
 └── M5#3  slot 3
```

- Slot 1 / 2: V2 tap / level fill on the 5m (path stamped `v2_…`)
- Slot 3: Hunt V3 / impulse_3 — third 5m must close through the **prior 15m high** (long) or **prior 15m low** (short)

No 1m C timing. Fill is on the 5m logic above.

---

## 4H weather (on for Hunt live)

File: `backend/src/brain/weather.py`  
Version: `WEATHER_V1B_RETRACE`

- `SWING_UP` → LONG only  
- `SWING_DOWN` → SHORT only  
- `CHOP` → both  

If 4H is swinging but price gives back **≥ 1.0 ATR** from the recent 4H
extreme (last 8 4H bars), flag becomes **CHOP**. Opposite side is not
blocked until a full 4H flip.

Hunt live loop: FIRE + weather forbids that side → `WEATHER_BLOCK`.
Scenario used to skip this gate. Hunt does not.

---

## SL / TP / size

- SL 1.5 × ATR  
- TP 2.5 × ATR  
- Isolated  
- Fit qty down to free USDT if margin is short  
- One Isolated position only  
- 15 min quiet after a hard reject  

Preview boxes read `last_hunt` entry / stop / target.

---

## Live path (current main)

```
full_analysis → evaluate_hunt_c_fi
        ↓
WAIT | FIRE
        ↓
weather + cooldown + flat Isolated + levels present
        ↓
_open_live_from_hunt → MEXC Isolated
```

`entry_engine` is forced `legacy`.  
`last_action` examples: `NO-TRADE (WAIT)`, `WEATHER_BLOCK`, `LIVE OPEN LONG Isolated order …`

---

## Key files

| Piece | File |
|---|---|
| Hunt C-FI | `backend/src/brain/observation_hunt_c_fi.py` |
| C-fast | `observation_hunt_c_fast.py` |
| Internal / slots | `observation_hunt_c.py` |
| Slot 3 V3 | `observation_hunt_v3.py` |
| V2 fill | `observation_hunt.py` |
| Weather | `backend/src/brain/weather.py` |
| Loop | `backend/src/autotrader_loop.py` |
| Isolated send | `backend/src/autotrader_exec.py` |
| UI hero | `BrainHeroPanel.js` |

Backup tag to cut: `hunt-live-2026-09-24` (create locally if not pushed yet).

---

## What Hunt is not

- Not forming-15m slot-1/2 + 1m C  
- Not S2 pullback continuation  
- Not S3 early reversal  
- Chart CHoCH/BOS paint is the same structure overlay Hunt reads — still not S1
