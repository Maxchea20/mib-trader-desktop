# Failed 5-minute retest, both directions

Simulated OHLC fills. Not exchange prints. No live order. No parameter was searched.

Same 15-minute range and same first 5-minute body breakout as V3. A failure is the first later 5-minute candle that closes back through that level before 11:00. A wick is not a failure. The fill is the next 1-minute open. Stop, target, fees, slippage, spread, and funding are the V3 preset.

Fade trades against the break: failed upside break is a short, failed downside break is a long. With takes the original break direction on that same candle.

Eligible sessions: 31. Same dates as the preserved V3 run.

## Short the failed retest (fade)

| Split | Closed | Win rate | Net | Fees | Avg R |
|---|---:|---:|---:|---:|---:|
| train | 9 | 66.7% | 73.19 | 15.06 | 0.80 |
| validation | 5 | 0.0% | -58.54 | 7.81 | -1.20 |
| out_of_sample | 4 | 25.0% | -18.21 | 6.32 | -0.45 |

## Long the original break anyway (with)

| Split | Closed | Win rate | Net | Fees | Avg R |
|---|---:|---:|---:|---:|---:|
| train | 9 | 11.1% | -75.72 | 13.99 | -0.87 |
| validation | 5 | 60.0% | 29.63 | 8.18 | 0.60 |
| out_of_sample | 4 | 50.0% | 11.54 | 6.41 | 0.30 |

## Reading

Fade closed 18 trades and netted 73.19 / -58.54 / -18.21. Taking the original direction on the same failure candles closed 18 trades and netted -75.72 / 29.63 / 11.54. These are the same signals with the side flipped. A gain on one side of a 7-session slice is not an edge. Fills are simulated OHLC, not exchange prints.
