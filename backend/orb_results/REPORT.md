# NY ORB results

These numbers are from `market_data_clean.db` (sha256 recorded in `data_audit.json`).
Nothing here was selected because it looked good out of sample.

Primary preset, fixed before the run: ORB-15, candle-close entry, long and short,
0.25% stop, 0.50% target, 1% equity risk, 5x isolated, 1 bp slippage and a 0.10
USDT spread. Starting equity 1,000 USDT on every split. BTC/USDT 5-minute
candles. New York 09:30–11:00 only, NYSE weekdays.

| Split | Dates | Closed trades | Win rate | Gross P&L | Fees | Execution drag | Net P&L | Profit factor | Avg R | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Train | 2025-09-16 to 2026-04-28 (155 sessions) | 175 | 32.6% | +4.82 | 225.81 | 114.17 | -335.17 | 0.71 | -0.22 | 35.7% |
| Validation | 2026-04-29 to 2026-07-13 (51) | 54 | 29.6% | -36.55 | 81.47 | 41.32 | -159.33 | 0.63 | -0.31 | 15.9% |
| Out of sample | 2026-07-14 to 2026-09-25 (53) | 61 | 31.1% | -20.18 | 89.39 | 45.31 | -154.87 | 0.67 | -0.27 | 17.9% |

The primary preset is negative on every split. Out of sample, longs lost 30.0 (35 trades, 37% wins) and shorts lost 124.9 (26 trades, 23% wins). In training both sides lost. In validation longs lost 142.4 and shorts lost 16.9.

The other 107 pre-declared cells were not a search.

- Train: 0 of 108 cells had positive net P&L. The best train result was still a loss (-8.4).
- Validation: 9 of 108 positive.
- Out of sample: 18 of 108 positive (median -50.5, worst -385.5, best +71.3).
- Positive on train, validation, and out of sample: 0.

Cells that made money out of sample lost money in training. They are not a strategy.

Costs dominate. On the primary train run the price path before costs was about +4.8 USDT
and fees plus slippage/spread removed about 340. That is not a claim that a zero-cost
version is tradable. There is no bid/ask history in the file.

Intrabar rows are OHLC proxies. One-minute data covers only 31 NYSE sessions
(2026-08-12 to 2026-09-25) and the same primary preset lost on all three of those
shorter splits (train -99.3, validation -36.1, out of sample -30.6).

No live order was sent.
