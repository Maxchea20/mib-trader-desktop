# Hunt C-fast

Branch: `hunt-c-fast`. **Not merged to main.** Main stays Hunt C.

## vs C

Same: 5m #3 through prior 15m high/low (or V2 tap on 5m #1/#2), fill at 15m level,
4h weather must allow, no 1% opposite sleeve, no BREAKOUT_DETECTED, SL 1.5 / TP 2.5.

Different: 15m setup may be **CHoCH or any BOS that is not extended** (BOS #3+ still skipped).
C only allowed CHoCH or the first BOS after CHoCH.

## 90d tape (16 Jun – 14 Sep 2026, $500, 2%, fees on)

- C: 68 trades, +$430, DD 10.8%
- C-fast: 100 trades, +$734, DD 10.6%

## Call

```python
from src.brain import evaluate_hunt_c_fast, HUNT_VERSION_C_FAST
```
