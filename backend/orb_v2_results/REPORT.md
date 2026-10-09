# ORB V2 results

Simulated OHLC fills. No live order was sent. Nothing here was selected because it made money.

Database `backend/market_data_clean.db` sha256 `84fdee4e54dea3ac8d075a4a06e7a3be75669bf46fb4058bf71e1d9c2348d26f`.

## Baseline reproducibility

The original primary preset was run again on the full 5-minute history: ORB-15, close entry, both sides, 0.25% stop, 0.50% target, 1% equity risk, 5x, 1 bp slippage, 0.10 USDT spread, 1,000 USDT restarted on every split.

Match against the committed `backend/orb_results/summary.json` primary rows: **yes**.
The same check on the committed 1-minute ORB-15 resolution run: **yes**.

Published 5-minute primary nets were train -335.17, validation -159.33, out of sample -154.87. The fresh primary run is in `summary.json` under `baseline_repro`. A full 108-cell rerun of `scripts/run_orb_backtest.py` in the same pass reproduced those primary cells.

## What V2 could actually see

V2 needs the 09:30 5-minute candle and the five 1-minute candles inside it. `1m` in this file runs from 2026-08-11 18:01 UTC to 2026-09-27 11:02 UTC and has one hole (2026-09-11 09:46 UTC through 2026-09-12 20:59 UTC). Earlier 5-minute history was not turned into fake 1-minute candles.

New York dates in the file: 378.
Usable V2 sessions: 31.
Range outcomes: `{"missing_opening_range": 1, "missing_1m_opening_range": 228, "not_nyse_session_day": 118, "ok": 31}`.

Each split below restarts at 1,000 USDT. A split does not inherit size from the previous one.

## V2 primary (distance filter off)

| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 17 | 29.4% | -13.15 | 27.28 | 13.84 | -54.28 | 0.62 | -3.19 | -0.32 | 8.2% |
| validation | 6 | 33.3% | 1.93 | 9.64 | 4.88 | -12.60 | 0.74 | -2.10 | -0.20 | 3.6% |
| out_of_sample | 6 | 33.3% | 1.75 | 9.50 | 4.80 | -12.55 | 0.74 | -2.09 | -0.20 | 3.6% |

| Split | Sessions | Long breakouts | Short breakouts | No breakout | 1m signals | Filled |
|---|---:|---:|---:|---:|---:|---:|
| train | 18 | 9 | 9 | 0 | 17 | 17 |
| validation | 6 | 2 | 4 | 0 | 6 | 6 |
| out_of_sample | 7 | 6 | 1 | 0 | 6 | 6 |

Breakouts with no qualifying 1-minute continuation before 11:00, so no order: 2026-08-27 (train), 2026-09-25 (out_of_sample).

Long versus short, closed trades only:

| Split | Long trades | Long net | Long win rate | Short trades | Short net | Short win rate |
|---|---:|---:|---:|---:|---:|---:|
| train | 9 | 12.82 | 44.4% | 8 | -67.10 | 12.5% |
| validation | 2 | 5.78 | 50.0% | 4 | -18.38 | 25.0% |
| out_of_sample | 5 | -0.56 | 40.0% | 1 | -12.00 | 0.0% |

## Same sessions, previous ORB engine

These two runs use the unchanged engine and the same risk, fees, and slippage. Entries are still the old close-and-next-open rule on 5-minute candles. The dates are the usable V2 sessions, not the full 2025-2026 sample.

### Previous primary, ORB-15

| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 21 | 28.6% | -21.94 | 32.83 | 16.62 | -71.39 | 0.60 | -3.40 | -0.34 | 9.8% |
| validation | 7 | 14.3% | -36.44 | 11.07 | 5.61 | -53.12 | 0.25 | -7.59 | -0.77 | 7.0% |
| out_of_sample | 7 | 42.9% | 22.01 | 11.16 | 5.64 | 5.20 | 1.11 | 0.74 | 0.09 | 2.4% |

### Previous ORB-5 close (same five-minute window, old entry rule)

| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 27 | 29.6% | -18.29 | 43.02 | 21.80 | -83.11 | 0.63 | -3.08 | -0.31 | 13.5% |
| validation | 8 | 25.0% | -16.84 | 12.77 | 6.47 | -36.08 | 0.50 | -4.51 | -0.45 | 5.9% |
| out_of_sample | 10 | 30.0% | -6.92 | 15.69 | 7.93 | -30.55 | 0.63 | -3.05 | -0.30 | 4.2% |

## Distance filter, separate

Primary leaves the filter off. This run rejects a next open that is more than one opening-range width beyond the broken level. It is not a tuned setting and it is not the comparison above.

| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 15 | 33.3% | 5.39 | 24.25 | 12.31 | -31.17 | 0.74 | -2.08 | -0.20 | 7.0% |
| validation | 3 | 33.3% | 0.97 | 4.84 | 2.45 | -6.32 | 0.74 | -2.11 | -0.20 | 2.4% |
| out_of_sample | 4 | 25.0% | -8.76 | 6.28 | 3.18 | -18.21 | 0.49 | -4.55 | -0.45 | 3.6% |

## Reading

On the only dates where the 1-minute continuation can be simulated, V2 net versus the previous ORB-15 primary was higher on train, validation. Versus the previous ORB-5 close entry it was higher on train, validation, out_of_sample. Higher on a six-week sample is not evidence of an edge. V2 nets were train -54.28, validation -12.60, out of sample -12.55. ORB-15 on those dates was -71.39, -53.12, 5.20. ORB-5 on those dates was -83.11, -36.08, -30.55. The ORB-15 out-of-sample gain on this window is seven sessions inside a full-sample result that lost 154.87. The full-sample baseline remains negative on every split. The separate 1x-range distance filter was also negative on every split and was not adopted. No parameter was changed to improve these numbers.

Fills are OHLC simulations (`ohlc_proxy=true`, `simulated_fill=true`), not exchange-confirmed prints. If a 1-minute bar trades both the stop and the target, the stop is taken and `path_ambiguous` is set.
