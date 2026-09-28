# Hunt / S1 / S2 / SLOT3 / C — Current Architecture

This document reflects the code currently deployed on `main`.

## Single brain

**Hunt is the only decision authority.**

Hunt owns:
- thesis
- direction
- invalidation
- one-ticket state
- FIRE authority

S1, S2, SLOT3 and C never place orders.

## Timing paths

```
                 HUNT THESIS
                     |
          +----------+----------+
          |          |          |
         S1         S2       SLOT3
       early      extension   confirmed
       path       path        path
          |          |          |
          +-----> C  <----------+
                  |
                FIRE
                  |
          autotrader_loop
                  |
        existing execution pipe
                  |
             MEXC Isolated
```

### S1
Early breakout path.

- Current forming 15M only.
- Uses the current 15M's 5M sections.
- Slot 1/2.
- Requires same-direction developing 15M BOS plus qualifying current M5 BOS.
- Starts C.
- C success -> Hunt FIRE.
- C miss -> WAIT. No fallback FIRE.

### SLOT3
Confirmed breakout fallback.

- Wait for a **closed 15M CHoCH/BOS**.
- Entry occurs in the **next 15M candle**.
- No C.
- Hunt FIRE directly.
- Same Hunt thesis / one-ticket authority as S1.

### S2
Separate extension/pullback path.

- Extension.
- Pullback.
- New same-direction M5 structural event.
- Starts C.
- C success -> Hunt FIRE.
- C miss -> WAIT.

### C
Entry timing only.

- Uses `find_m5_2_intrabar_entry`.
- Reads closed 1M candles.
- Returns SUCCESS / MISS / NONE.
- Never creates a thesis.
- Never fires an order.

## Execution

```
Hunt FIRE
  -> autotrader_loop
  -> weather / one-position / cooldown guards
  -> _open_live_from_s1
  -> sizing
  -> MEXC Isolated
```

Paper execution uses the same FIRE output through the paper-trading pipe.

## Lifecycle

After FIRE, the existing lifecycle brain owns HOLD / EXIT handling, including structural invalidation and hard SL/TP checks.

## Removed legacy architecture

The repository no longer uses:
- ScenarioEngine
- scenario_live_bridge
- old S1 engine
- old S1 timing layer
- C watcher
- S3 early-reversal experiment
- old Scenario bridge tests

The compatibility layer is intentionally gone. The live path points at Hunt directly.

## Validation still required

Historical replay must exercise this exact architecture before treating performance as validated.
