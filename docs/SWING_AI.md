# Swing AI (paper-mode experiment)

A separate, isolated swing-trading experiment. **The AI is the decision brain; deterministic code owns all money
decisions.** Nothing here imports Hunt, S1/S2, the scalp engine or Trend Break. Delete `backend/src/swing_ai/` (and the
three small hooks in `server.py`) to remove it completely.

```
live MEXC feed + SQLite candles
   -> market_state.build_snapshot()      multi-timeframe facts, causal (closed candles only)
   -> events.detect()                    deterministic wake-ups (no AI on ticks)
   -> engine.review_entry / review_manage  strict-JSON call to the model
   -> risk.validate_*()                  accept exactly as proposed, or reject with reasons; sizes the position
   -> paper.py                           simulated fills, SL/TP, MFE/MAE, R after fees
   -> store.py                           every snapshot, event, decision and position is logged
```

## Run it (paper only)

```
set OPENAI_API_KEY=...            # PowerShell: $env:OPENAI_API_KEY="..."
set SWING_AI_ENABLED=1
set SWING_AI_MODEL=gpt-5.4-mini   # optional; falls back to AI_THESIS_MODEL
python run_server.py
```

* Try one review without trading or storing anything: `python scripts/swing_ai_dryrun.py` (add `--no-call` to only size the snapshot).
* Watch it: `GET /api/swing-ai/status`, `/api/swing-ai/decisions`, `/api/swing-ai/positions`.
* Judge it: `python scripts/swing_ai_report.py` (win rate, net R after fees, profit factor, t-statistic, MFE/MAE, rejected proposals).

There is **no live execution path**. A live executor would be a separate, explicit step that reuses the MEXC layer
(`autotrader_exec.py` / `mexc_private.py`) behind the same `risk.py` checks.

## What the AI sees (all known at the decision timestamp)

* **Market:** live price, bid/ask, spread %, ticker age.
* **4H / 1H / 15M:** the last 30 / 48 / 64 closed candles, ATR and volatility percentile, trend (from confirmed swings),
  BOS / CHoCH, swing highs and lows, support/resistance clusters with touches and distance in ATR, RSI, multi-bar returns
  in ATR, volume ratios, range expansion, extension from EMA20/EMA50 and position in the 96-bar range.
  15M also has liquidity sweeps and wick rejections.
* **5M / 1M:** entry refinement only (EMA9 reclaim, wicks, immediate structure).
* **Derived:** trend alignment across 4H/1H/15M, volatility regime, whether price is extended, room to the nearest
  level in 1H-ATR, momentum flips, typical 24-bar range.
* **Open position:** side, entry, stop, target, thesis, invalidation, unrealised R, MFE/MAE.

## Cadence

* Heartbeat: full entry review every 15 minutes (when flat).
* Events wake it sooner: 4H/1H structure change, 15M BOS/CHoCH, 15M liquidity sweep, a 15M close through a multi-touch
  level, volatility expansion, 1H RSI crossing 50, price arriving at a multi-touch level. Per-event de-duplication and a
  15-minute quiet period per kind; at least 60 s between AI calls.
* Open position: management review every 5 minutes; **immediately** when price crosses the invalidation level or comes
  within 0.25 ATR of the stop.

## Risk / execution layer (`risk.py`, defaults in `config.py`)

Max 1% equity risk per trade, max 5x leverage, max position size, stop 0.5 to 5 x ATR(1h), reward/risk at least 1.5,
spread <= 0.03%, fresh ticker, one position at a time, 3 trades/day, -3R daily stop, cooldown after a loss, limit orders
must be on the passive side and within 2 ATR(1h), pending limits expire after 8 h. The AI never sets size or leverage and
its levels are never edited: a proposal is valid as given or rejected. A failed or unparseable AI call is a NO_TRADE / HOLD.
The AI can `MOVE_SL` only to reduce risk, and `EXIT`; it cannot add or reverse (a reversal suggestion is only logged).

Fees are the MEXC BTCUSDT perpetual tier: taker 0.02%, maker 0% (market entry and stop-outs pay taker, limit entries and
targets pay maker).

## Paper fills are conservative

Pending limits fill only when a later **closed** 1m bar trades through the level; on the fill bar only the stop counts;
a bar touching both stop and target counts as the stop; no slippage is added (so real results will be a little worse).

## How to read the result honestly

* Only **forward** paper trading is a valid test. Replaying old data through an LLM is contaminated: the model may have
  seen those prices and events in training, so a good replay proves nothing.
* At about 1 to 3 trades a week per setup the sample grows slowly. With a per-trade standard deviation near 1.3 R you need
  roughly 150+ closed trades to detect +0.2 R/trade; `swing_ai_report.py` prints the number. Do not act on 20 trades.
* Do not tune prompts, thresholds or the model to the results you have seen. Change one thing, log the version, and
  restart the count.
* Kill it if the expectancy after fees is not clearly positive at a meaningful sample size. Everything is in one folder.

## Cost

Each review sends roughly 6-8k tokens. A 15-minute heartbeat is about 96 reviews a day, plus management reviews every
5 minutes while a position is open (up to 288 a day). Watch your API usage in the first days.
