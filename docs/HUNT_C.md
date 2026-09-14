# Hunt C

Locked book from the Jul–Sep 2026 tests. **Do not retune per month.**

## Rules

- Version: `OBSERVATION_HUNT_M5_C`
- Map: prior 15m high / low
- Trigger: 5m #3 of the live 15m closes through that map (`impulse_3` / `hold_3`)
- Gate: that 15m must be a V2 arm with **CHoCH** or **first BOS after CHoCH**
- No `BREAKOUT_DETECTED` door
- 4h weather must allow the side (CHOP allows both). No 1% opposite-weather sleeve
- Fill **at the 15m level**, not the 5m close
- SL 1.5 × 15m ATR / TP 2.5 × 15m ATR
- Size FULL (caller uses 2% equity risk)
- After FIRE: existing V1b lifecycle

## Call

```python
from src.brain import evaluate_hunt_c, HUNT_VERSION_C

fire = evaluate_hunt_c(
    candles_15m,   # last closed 15m last; on 5m #3 that bar just completed
    candle_5m,     # this closed 5m
    live_5ms,      # 1..3 fives inside the parent 15m
    candles_4h=candles_4h,
    candles_1h=candles_1h,
)
```

V2 `evaluate_hunt` on main is unchanged.
