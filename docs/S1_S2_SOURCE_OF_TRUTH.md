# S1 / S2 — SOURCE OF TRUTH

Updated 2026-09-24.

Live Isolated today: **Hunt C-FI only**. S1 / S2 / C are **not** wired.
Last S1/S2 code: commit `c77239d`.

**How they sit in Hunt (locked sit, not live yet)**
S1 / S2 / C are Hunt *fill / timing*, not a second engine.
See `docs/HUNT_C_FI_SOURCE_OF_TRUTH.md` § TARGET UPGRADE.

- Hunt = thesis + direction + weather + Isolated
- S1 = slot 1 / 2 of a forming 15m + C
- Slot 3 = same C on Hunt’s slot-3 line (prior 15m H/L)
- S2 = after extension: measured pullback + new 5m + C
- C = first 1m close across that 5m’s line. All three slots.

Do not merge S1 into S2. Do not let C create a thesis.

---

## Hard rules

- S1, S2, S3 are **separate** pathways. Do not merge conditions.
- DETECTION ≠ CONFIRMATION ≠ FIRE ≠ EXECUTION
- C is 1m entry timing on Hunt’s 5m, not Hunt confluence and not a new thesis.
- M15 BOS/CHoCH in this spec does **not** always mean the 15m candle has closed.
- No lookahead. No fixed “minute 3”. C = first qualifying 1m **close**.
- S3 was designed. S3 was **never wired live**.

---

## S1 — DIRECT CONTINUATION

Purpose: catch continuation early, while the 15m is still forming.

```
forming 15m (context / developing structure)
        ↓
slot 1 or slot 2 of THAT same 15m prints M5 BOS/CHoCH same direction
        ↓
C watches ONLY that 5m’s five 1m candles
        ↓
first 1m CLOSE across the relevant line
        ↓
FIRE → Isolated
```

LONG:  M15 bullish BOS/CHoCH → M5 bullish BOS/CHoCH → C → LONG
SHORT: M15 bearish BOS/CHoCH → M5 bearish BOS/CHoCH → C → SHORT

- Slot 3 is not S1. Slot 3 may still use the same C on Hunt’s prior-15m high/low line.
- Old M15 event must not stay valid forever.
- M1 is timing only (C). M1 does not replace the 15m/5m setup.

Why it exists: waiting for a **closed** 15m BOS is often too late.

---

## S2 — BREAKOUT → EXTENSION → PULLBACK → FRESH CONFIRMATION

Purpose: do not chase. Wait for a real pullback, then a new 5m.

```
M15 BOS/CHoCH
        ↓
M5 breakout / extension (too far to enter)
        ↓
actual measured pullback (frozen ATR at extension, not “distance shrank”)
        ↓
fresh M5 confirmation (new event id — spent M5 cannot re-fire)
        ↓
C on that fresh 5m
        ↓
FIRE → Isolated
```

Pullback is price moving back toward origin, enough vs frozen ATR
(0.25–0.50 ATR zone in the last engine), and located near origin / S-R / FVG.
Live ATR getting smaller is not a pullback.

---

## C — 1m entry timing (S1, S2, and Hunt slot 3)

When the qualifying M5 is identified:

- Watch the 1m candles **inside that M5 only**
- Enter on the **first** 1m CLOSE that confirms the structural cross
- Not hardcoded to minute 1, 2, 3, 4, or 5
- Backtest median was often minute 1 or 3. That is history, not a rule

---

## S3 — EARLY REVERSAL (spec only, not live)

```
M5 exhaustion / reversal context
        ↓
M5 reversal BOS/CHoCH
        ↓
later M1 directional confirmation
        ↓
FIRE
```

M15 BOS/CHoCH is **not** required for S3.
Do not wire S3 live until it is backtested and the user says so.

---

## Last implementation map (deleted from main)

| Piece | Last file |
|---|---|
| Classifier / thesis / scenarios | `backend/src/brain/scenario_engine.py` |
| S1/S2 → C → FIRE | `backend/src/scenario_live_bridge.py` |
| C watcher | `backend/src/brain/entry_timing_c_watcher.py` (may still be on disk, unused) |
| Isolated send | `_open_live_from_hunt` in `autotrader_exec.py` (Hunt name, shared pipe) |

Engine names in that code:

- S1 = `FRESH_CLEAN_BREAKOUT`
- S2 = `FRESH_PULLBACK_CONTINUATION`

---

## Do not do

- Do not blend S1 into S2 or S3
- Do not treat Hunt FIRE as S1 FIRE
- Do not wait for 15m close before starting S1/S2 detection
- Do not hardcode C to one 1m bar
- Do not put S3 on Isolated without a new explicit order
