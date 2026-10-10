# ORB V3 rule B, side by side with the current entry

Simulated OHLC fills. These are not exchange-confirmed prints. No live order was sent. No parameter was searched.

The current entry is unchanged: after the 5-minute body breakout, the first later 1-minute candle that is green and closes above OR_HIGH, or red and closes below OR_LOW, is the signal.

Rule B uses the same 15-minute range, the same 5-minute body breakout, the same 11:00 cutoff, and the same costs. The only change is the 1-minute trigger. Only the five 1-minute bars in the next 5-minute candle are eligible. A long must close above its open and above the breakout candle's close. A short must close below its open and below that close. If none of the five qualifies, there is no trade.

Eligible dates are the 31 tradable sessions from the current V3 survey. Splits are the same chronological 60/20/20 cut, and each split restarts at 1,000 USDT.

## Costs, both sides

`{"stop_loss_fraction": 0.0025, "take_profit_fraction": 0.005, "risk_fraction": 0.01, "leverage": 5.0, "max_daily_loss_fraction": 0.03, "starting_equity": 1000.0, "slippage_bps": 1.0, "spread_usd": 0.1, "compound": true, "funding_rate": 1.5e-05, "funding_interval_hours": 8, "entry_cutoff_local": "11:00:00"}`

## Current entry

| Split | Closed | Win rate | Gross | Fees | Funding | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 14 | 28.6% | -14.80 | 22.20 | 0.00 | 11.25 | -48.25 | 0.60 | -3.45 | -0.34 | 8.1% |
| validation | 6 | 16.7% | -27.25 | 9.54 | 0.00 | 4.83 | -41.62 | 0.30 | -6.94 | -0.70 | 5.9% |
| out_of_sample | 7 | 71.4% | 84.32 | 11.45 | 0.00 | 5.79 | 67.08 | 3.72 | 9.58 | 0.94 | 2.4% |

## Rule B

| Split | Closed | Win rate | Gross | Fees | Funding | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 10 | 30.0% | -6.65 | 15.87 | 0.00 | 8.03 | -30.56 | 0.63 | -3.06 | -0.30 | 7.0% |
| validation | 4 | 25.0% | -8.54 | 6.43 | 0.00 | 3.25 | -18.23 | 0.50 | -4.56 | -0.45 | 3.6% |
| out_of_sample | 4 | 75.0% | 51.95 | 6.47 | 0.00 | 3.27 | 42.21 | 4.46 | 10.55 | 1.05 | 1.2% |

## Net difference (B minus current)

| Split | Current closed | B closed | Current net | B net | Difference |
|---|---:|---:|---:|---:|---:|
| train | 14 | 10 | -48.25 | -30.56 | 17.69 |
| validation | 6 | 4 | -41.62 | -18.23 | 23.40 |
| out_of_sample | 7 | 4 | 67.08 | 42.21 | -24.87 |

## Same breakout, different entry

Both closed a trade: 2026-08-12, 2026-08-13, 2026-08-19, 2026-08-21, 2026-08-24, 2026-08-25, 2026-08-26, 2026-08-28, 2026-09-01, 2026-09-03, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-15, 2026-09-18, 2026-09-21, 2026-09-23, 2026-09-24.

Current closed a trade and B did not: 2026-08-14, 2026-08-18, 2026-08-31, 2026-09-04, 2026-09-14, 2026-09-16, 2026-09-17, 2026-09-22, 2026-09-25.

B closed a trade and the current entry did not: none.

Where both closed, the signal is the same minute on 18 dates and a different minute on 0 dates.

## Reading

The preserved current entry still nets -48.25 / -41.62 / 67.08 on 27 closed trades. Rule B nets -30.56 / -18.23 / 42.21 on 18 closed trades. On every date both closed, the 1-minute signal is the same minute. B did not change a fill. It skipped 9 current trades and added 0. The skipped trades netted -17.41 on train (1 win of 4), -23.53 on validation (0 of 2), and 24.52 on the out-of-sample split (2 wins of 3). Train and validation look less bad because those skipped trades lost money. The out-of-sample split looks worse because the skipped trades there made money. This is the same six-week file. It is not evidence of an edge, and it was not used to change a threshold. Fills are simulated OHLC, not exchange prints.

Fills are OHLC simulations. Funding is the flat +0.0015% / 8h snapshot from 2026-10-09, not the historical path. A same-bar stop and target is a stop.
