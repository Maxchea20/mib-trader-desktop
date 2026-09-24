# HUNT C-FI — SOURCE OF TRUTH

Updated 2026-09-24.

Two layers in this file:

1. **LIVE NOW** — what Isolated follows today
2. **TARGET UPGRADE** — how S1 / S2 / C sit *inside Hunt* (not a second engine)

Do not ship the target until the user says to code it.

S1/S2 original definitions: `docs/S1_S2_SOURCE_OF_TRUTH.md`.

---

## Intention (locked)

Hunt was late. S1/S2 were meant to **upgrade Hunt entry timing**, not become
a new brain. That accident is what got ripped off live.

Hunt decides **if** there is a trade (thesis + direction + weather + risk).
S1 / S2 / C only decide **how early** the same trade is sent.

One FIRE. One Event Stream. One Isolated pipe.

---

## TARGET UPGRADE — S1 / S2 / C inside Hunt

```
Hunt thesis (may arm while the 15m is still forming)
        ↓
the 5m Hunt is watching (slot 1 or 2 or 3)
        ↓
C watches that 5m’s five 1m candles
        ↓
first 1m CLOSE across that slot’s line  →  send Isolated
        ↓
that 5m ends with no 1m close  →  no fire this slot
        ↓
next slot may try C again on its own line
        ↓
if already extended  →  S2: measured pullback + NEW 5m + C
        ↓
thesis dies  →  cancel C, no order
```

**S1** = Hunt fill on slot 1 / 2 of the (forming) 15m + C.
**Slot 3** = same C, Hunt’s slot-3 line (prior 15m high long / prior 15m low short).
**S2** = Hunt “too far” path: freeze ATR at extension, real pullback, new 5m, same C.
Spent 5m cannot re-fire.

C does not invent a thesis. C does not change the line.
C = first qualifying 1m close inside the 5m Hunt already chose.
Not hardcoded to minute 1 or 3.

Must-have or it stays late:

- Hunt may arm on a **forming** 15m
- Slot 3 fallback still exists (C on slot 3, not only impulse_3 close)
- Shared invalidation (thesis dead → C dead)

Open calls (do not assume):

- 4H weather on slot 1/2 C: still Hunt’s V1b unless the user changes it
- S3 still not live

---

## LIVE NOW (do not confuse with the target)

Version: `OBSERVATION_HUNT_M5_C_FI`
`entry_engine`: `legacy`

Gates: C-fast / Internal / Rearm.
Fill today: slot 1/2 V2 tap; slot 3 impulse_3 close through prior 15m H/L.
**No 1m C on live.** Weather V1b on. Isolated via `_open_live_from_hunt`.

---

## 4H weather (live)

`WEATHER_V1B_RETRACE`
SWING_UP → LONG only. SWING_DOWN → SHORT only. CHOP → both.
≥ 1.0 ATR retrace off the recent 4H extreme → CHOP.

---

## SL / TP / size (live)

SL 1.5 ATR. TP 2.5 ATR. Isolated. Fit-to-balance. One position.
15 min quiet after a hard reject.

---

## Key files (live)

| Piece | File |
|---|---|
| Hunt C-FI | `backend/src/brain/observation_hunt_c_fi.py` |
| Weather | `backend/src/brain/weather.py` |
| Loop | `backend/src/autotrader_loop.py` |
| Isolated | `backend/src/autotrader_exec.py` |

Last S1/S2 code (deleted from live): commit `c77239d`.
