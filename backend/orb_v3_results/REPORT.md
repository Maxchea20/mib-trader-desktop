# ORB V3 results

Fresh run. The previous V2 trade log used the 09:30-09:35 five-minute candle as the opening range and is not this result.

Simulated OHLC fills. These are not exchange-confirmed prints. No live order was sent. No parameter was searched.

Database `backend/market_data_clean.db` sha256 `84fdee4e54dea3ac8d075a4a06e7a3be75669bf46fb4058bf71e1d9c2348d26f`.

## Definition

15-minute candle 09:30-09:45 America/New_York sets OR_HIGH and OR_LOW. From 09:45, the first completed 5-minute close strictly outside that range locks the direction. A wick is not enough. The first completed 1-minute continuation after that close is the signal: bullish and still above OR_HIGH for a long, bearish and still below OR_LOW for a short. The fill is the next 1-minute open, and only before 11:00. There is no retest and no direction flip.

## What this file can actually test

`15m` runs 2025-09-15T20:15:00+00:00 to 2026-09-27T11:00:00+00:00 (36156 bars). `5m` runs 2025-09-15T19:05:00+00:00 to 2026-09-27T11:00:00+00:00 (108480 bars). `1m` runs 2026-08-11T18:01:00+00:00 to 2026-09-27T11:02:00+00:00 (65150 bars) and has one hole (2026-09-11 09:46 UTC through 2026-09-12 20:59 UTC). V3 needs that real 1-minute continuation, so earlier sessions were not traded and no 1-minute candle was built from 5-minute OHLC. 2026-08-11 has a 15-minute range but the 1-minute file starts after the New York open. 2026-09-11 is inside the hole. Both were skipped.

New York dates seen in 15m, 5m, or 1m: 378.
Tradable V3 sessions (15m range, complete 5m path, real 1m entry window): 31.
Range outcomes: `{"missing_opening_range": 1, "ok": 259, "not_nyse_session_day": 118}`.

Each split restarts at 1,000 USDT. A split does not inherit size or losses from the previous one.

## V3 primary

| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 14 | 28.6% | -14.80 | 22.20 | 11.25 | -48.25 | 0.60 | -3.45 | -0.34 | 8.1% |
| validation | 6 | 16.7% | -27.25 | 9.54 | 4.83 | -41.62 | 0.30 | -6.94 | -0.70 | 5.9% |
| out_of_sample | 7 | 71.4% | 84.32 | 11.45 | 5.79 | 67.08 | 3.72 | 9.58 | 0.94 | 2.4% |

| Split | Sessions | Long breakouts | Short breakouts | No breakout | 1m signals | Filled |
|---|---:|---:|---:|---:|---:|---:|
| train | 18 | 10 | 5 | 3 | 14 | 14 |
| validation | 6 | 4 | 2 | 0 | 6 | 6 |
| out_of_sample | 7 | 5 | 2 | 0 | 7 | 7 |

Breakouts with no qualifying 1-minute continuation before 11:00, so no order: 2026-08-27 (train).

Long versus short, closed trades only:

| Split | Long trades | Long net | Long win rate | Short trades | Short net | Short win rate |
|---|---:|---:|---:|---:|---:|---:|
| train | 10 | -30.46 | 30.0% | 4 | -17.79 | 25.0% |
| validation | 4 | -17.62 | 25.0% | 2 | -24.00 | 0.0% |
| out_of_sample | 5 | 30.06 | 60.0% | 2 | 37.02 | 100.0% |

## Same dates, unchanged original ORB-15 engine

This is not V3. It is the original close-entry engine, ORB-15 built from the three 5-minute candles inside 09:30-09:45, same risk, fees, and slippage, same session dates, each split restarted at 1,000 USDT. On these dates the 15-minute candle and that three-candle range matched.

| Split | Closed | Win rate | Gross | Fees | Execution drag | Net | Profit factor | Expectancy | Avg R | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train | 21 | 28.6% | -21.94 | 32.83 | 16.62 | -71.39 | 0.60 | -3.40 | -0.34 | 9.8% |
| validation | 7 | 14.3% | -36.44 | 11.07 | 5.61 | -53.12 | 0.25 | -7.59 | -0.77 | 7.0% |
| out_of_sample | 7 | 42.9% | 22.01 | 11.16 | 5.64 | 5.20 | 1.11 | 0.74 | 0.09 | 2.4% |

## Reading

Train net was -48.25 and validation net was -41.62. Out-of-sample net was 67.08. That out-of-sample gain is seven closed trades after two losing splits, inside a six-week 1-minute file. It is not evidence of an edge and it was not used to change a threshold. The unchanged original ORB-15 close engine on the same dates was -71.39, -53.12, 5.20. Each figure restarts from 1,000 USDT. Fills are simulated OHLC, not exchange prints.

Fills are OHLC simulations (`ohlc_proxy=true`, `simulated_fill=true`). If a 1-minute bar trades both the stop and the target, the stop is taken and `path_ambiguous` is set. A 1-minute hole while a position is open leaves that trade unresolved instead of marking it across the hole.
