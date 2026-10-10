# 100 USDT, risk 10%, target 20%

Same dates and same entry rules. Starting equity is 100 and restarts on every split. Each trade risks 10% of the equity at entry. The target is twice that, about 20% of equity before fees. The price stop is still 0.25% and the price target is still 0.50%. Leverage is 50x so the 10% bet fits. At 5x it would not.

First current-entry trade risked 9.98 USDT, which is 10.0% of the 100 start.

Net is USDT and also the percent of that split's 100 restart.

## Current V3 entry

| Split | Closed | Win rate | Net USDT | Net vs 100 | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| train | 14 | 29% | -46.05 | -46.1% | -0.34 | 59.1% |
| validation | 6 | 17% | -37.72 | -37.7% | -0.70 | 47.2% |
| out_of_sample | 7 | 71% | 77.01 | 77.0% | 0.94 | 22.5% |

## Rule B

| Split | Closed | Win rate | Net USDT | Net vs 100 | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| train | 10 | 30% | -32.89 | -32.9% | -0.30 | 53.5% |
| validation | 4 | 25% | -19.61 | -19.6% | -0.45 | 31.9% |
| out_of_sample | 4 | 75% | 44.46 | 44.5% | 1.05 | 12.0% |

## Fade the failed retest

| Split | Closed | Win rate | Net USDT | Net vs 100 | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| train | 9 | 67% | 83.67 | 83.7% | 0.80 | 12.0% |
| validation | 5 | 0% | -47.20 | -47.2% | -1.20 | 47.2% |
| out_of_sample | 4 | 25% | -19.58 | -19.6% | -0.45 | 22.5% |

## Stay with the break after the failed retest

| Split | Closed | Win rate | Net USDT | Net vs 100 | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| train | 9 | 11% | -57.54 | -57.5% | -0.87 | 57.5% |
| validation | 5 | 60% | 27.08 | 27.1% | 0.60 | 12.1% |
| out_of_sample | 4 | 50% | 7.79 | 7.8% | 0.30 | 22.5% |

Fees and slippage still take a win from 2R down to about 1.8R and a loss from 1R to about 1.2R. On this account that is roughly +18% and -12% of the equity at the time of the trade, not a clean +20% and -10%. One loss is larger than the old 3% daily limit, but these rules still take only one trade a day, so that limit does not block the entry.

Simulated OHLC fills. Not exchange prints. Changing the bet size does not change whether the next week repeats.
