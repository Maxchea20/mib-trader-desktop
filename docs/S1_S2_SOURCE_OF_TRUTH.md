# S1 / S2 — SOURCE OF TRUTH

Saved 2026-09-24 so this is not lost in chat.

Live engine as of this date: **Hunt C-FI**. S1 / S2 / C are **not** on Isolated.
Last code that still contained S1/S2: git commit `c77239d`.
Deleted from `main` after that. Do not mix these rules into Hunt.

User intent: keep this spec. New S1/S2 plan comes later. Do not invent blends.

---

## Hard rules

- S1, S2, S3 are **separate** pathways. Do not merge conditions.
- DETECTION ≠ CONFIRMATION ≠ FIRE ≠ EXECUTION
- Hunt C-FI is a different engine. Do not redefine “C” as Hunt confluence.
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

- Slot 3 is outside the early window. S1 does not take slot 3.
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

## C — 1m entry timing (S1 and S2 only)

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

Thesis / scenario / execution-event were kept separate on purpose
(2026-09-22) so “distance recovered” could not fake a pullback.

Forming 15m: built from that 15m’s closed 5m + live 5m so BOS/CHoCH
could be seen **inside** the 15m, not only after close.

S1 live slots: 1 and 2 only (`enabled_m5_slots`).

Scenario live path **skipped** the 4H weather gate. Hunt live **uses**
`WEATHER_V1B_RETRACE` (1.0 ATR retrace turns SWING_* into CHOP).

---

## Why live S1/S2 rarely sent

Chart CHoCH/BOS paint ≠ scenario thesis.
WAIT / “No qualifying 15M structural break” was the usual state.
Slot 3 ignored. C needed a 1m close across the line.
~34 C-style fires in ~41 days in the isolated timing test.
That dryness was the rules, not a dead MEXC pipe.

---

## Do not do

- Do not blend S1 into S2 or S3
- Do not treat Hunt FIRE as S1 FIRE
- Do not wait for 15m close before starting S1/S2 detection
- Do not hardcode C to one 1m bar
- Do not put S3 on Isolated without a new explicit order
