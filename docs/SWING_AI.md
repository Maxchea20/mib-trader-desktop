# Swing AI (paper-mode experiment)

**AI is the brain. MIB is the body.** OpenAI (GPT) is the ONLY market-analysis brain. MIB moves raw MEXC data, stores and
displays what GPT says, enforces hard safety limits and simulates paper fills. MIB computes **no** second market opinion.
Nothing here imports Hunt, S1/S2, the scalp engine or Trend Break. Delete `backend/src/swing_ai/` (plus the small hooks in
`server.py`, `App.js`, `lib/api.js`, and `SwingAiPanel.js`) to remove it.

```
MEXC -> raw live data (quote, OHLCV 1D/4H/1H/15M/5M/1M) -> MIB raw-data layer (raw_data.py: packaging only)
     -> GPT analyses EVERYTHING -> strict JSON decision
     -> MIB stores it (swing_ai_market_snapshots / swing_ai_decisions / swing_ai_trades)
     -> Swing AI panel shows the stored GPT output
     -> deterministic safety layer (risk.py) -> paper broker (live MEXC execution is NOT connected)
```

## What MIB sends to GPT (raw evidence only)

Live price, bid, ask, spread, 24h exchange volume; closed OHLCV candles for 1D (90), 4H (180), 1H (240), 15M (192), 5M (144),
1M (90); and the current paper position/order (side, entry, stop, target, size, unrealised R, MFE/MAE) with GPT's **own** stored
thesis. GPT also gets its own previous analysis for continuity. There is no trend, structure, HH/HL, BOS/CHoCH, level,
liquidity, FVG, momentum, volatility, indicator, bias, confidence or signal from MIB. A test enforces this (no analytical key in
the snapshot, and no analytical function anywhere in the package).

## What GPT returns (strict JSON schema)

`decision` (LONG / SHORT / NO_TRADE), `confidence`, `headline` (one-line bottom line), `market_state` (TRENDING_UP / TRENDING_DOWN / RANGE / TRANSITION / UNCLEAR),
`daily_analysis`, `h4_analysis`, `h1_analysis`, `m15_analysis`, `structure_analysis`, `entry_analysis`, `entry_type`, `entry`,
`sl`, `tp`, `thesis`, `invalidation`, `invalidation_price`, and `wake_levels` (up to 4 prices where it wants to be woken next).
NO_TRADE is valid and still carries the full analysis. Every field is asked to be one short sentence, and the prompt (`swing-v3-brief`) tells GPT that paper mode exists to learn from its decisions, so a LIMIT order at a range edge or pullback it identified is a valid trade, not just NO_TRADE. Confidence is recorded for calibration and is not a MIB gate. `market_state` is an enum so results can be grouped by the AI's own
market classification. An unparseable or failed reply is a NO_TRADE / HOLD, never a guess.

## When GPT is called

* Heartbeat: a full review every 15 minutes when flat.
* Wakes (raw mechanical conditions, never an opinion): a 1H / 4H / 1D candle just closed; a raw price move of 0.6% within
  30 minutes; price crossing a level **GPT itself asked** to be woken at; open trade: price through GPT's own invalidation
  price, or within 0.15% of the stop (urgent, immediate). Each kind has a cooldown; AI calls are at least 60 s apart
  (urgent wakes ignore both).
* Open position: management review every 5 minutes. GPT may HOLD, MOVE_SL (risk-reducing only) or EXIT. It cannot add or reverse.

## Storage (audit trail of what GPT saw and decided)

* `swing_ai_market_snapshots`: the exact raw package sent (zlib-compressed JSON).
* `swing_ai_decisions`: timestamp, symbol, model, prompt version, wake reason, snapshot reference, the full structured GPT
  output (all analysis fields, levels, confidence, thesis, invalidation, alerts), the raw reply, the safety-layer verdict.
* `swing_ai_trades`: paper trades with fills, fees, MFE, MAE, gross/net R, exit reason, and final outcome.
* `swing_ai_wakes`: which raw condition triggered each review.

## UI

The Swing AI panel (`SwingAiPanel.js`, fed by `GET /api/swing-ai/latest`) shows Market view (Daily, 4H, 1H, 15M), Structure,
Entry analysis, the AI decision (entry, SL, TP, confidence), Thesis, Invalidation, GPT's own alerts, last-analysis time, the
open paper trade, GPT's latest management review, and performance. Every analysis field is the stored GPT text.

## Safety layer (`risk.py`) - constraints, not market opinions

Percent-of-price limits only (no volatility measure): stop 0.3% to 8%, reward/risk >= 1.5, market entry within 0.05% of price,
limit entries passive and within 3%, spread <= 0.03%, fresh ticker, one position at a time, 3 trades/day, -3R daily stop,
cooldown after a loss (the AI's confidence is not gated), 1% equity risk per trade, max 5x leverage, max notional. GPT's levels are never edited: valid as given
or rejected with reasons. GPT is told these limits up front so its proposals fit them.

## Controls in the Swing AI panel

* **AUTO-TRADE ON/OFF**: switches the AI reviews on or off (persisted in `swing_ai_settings.json` next to the database; `SWING_AI_ENABLED=1`
  is only the default before you touch it). While OFF, no AI calls are made, but open paper positions keep being tracked (SL/TP).
* **MODE PAPER/LIVE**: LIVE only *records the request* and opens the **LIVE INPUTS** block (MEXC balance read-out, risk % per trade,
  max leverage, max position size). Swing AI has **no live order path**, so the effective mode is always PAPER; a banner says so.
  Live execution would be a separate, reviewed build. The inputs already size every paper trade.

## Run it (paper only)

```
set OPENAI_API_KEY=...            # PowerShell: $env:OPENAI_API_KEY="..."
set SWING_AI_ENABLED=1
set SWING_AI_MODEL=gpt-5.4        # or another model; defaults to AI_THESIS_MODEL / gpt-5.4-mini
python run_server.py
```

* One review without trading or storing: `python scripts/swing_ai_dryrun.py` (`--no-call` sizes the raw snapshot only).
* Judge it: `python scripts/swing_ai_report.py` - win rate, expectancy, average R, MFE/MAE, fees, LONG vs SHORT, performance
  by GPT's market_state, confidence calibration, decision and NO_TRADE frequency.

## Paper fills are conservative

Pending limits fill only when a later **closed** 1m bar trades through the level; on the fill bar only the stop counts; a bar
touching both stop and target counts as the stop; no slippage. Fees: MEXC BTCUSDT taker 0.02%, maker 0%.

## Reading the result honestly

* Only **forward** paper trading is valid. Replaying old data through an LLM is contaminated by what the model saw in training.
* Roughly 150+ closed trades are needed to detect +0.2 R/trade; the report prints the number for the observed variance.
* Do not tune prompts, limits or the model to results you have seen. Change one thing, record the prompt version, restart the count.
* Cost: each review sends roughly 12-14k tokens of candles; 96 heartbeats a day plus up to 288 management reviews while in a trade.

## Cost controls

Reviews are the cost: about 13k input tokens plus GPT's reasoning tokens each. Levers, all in the panel's COST CONTROLS row (persisted):

* **Reasoning** `low` cuts the hidden reasoning tokens, usually the largest part of the bill (`default` = model default).
* **Review every** (heartbeat) 15 to 30 or 60 minutes; **Trade check every** for open positions.
* **Context** `COMPACT` sends about 45% fewer candles.
* **Prompt caching:** the request is ordered so the static prompt and slow-changing candles come first and everything that changes every call
  (wake reason, previous analysis, live quote, time) comes last, so OpenAI can discount the repeated prefix.
* **Slim candle rows** (prompt `swing-v4-slim`): rows are `[open,high,low,close,volume]` with no per-row timestamp; each timeframe carries `first_open_ts`, `last_open_ts`, `step_seconds` and
  `time_breaks` (any missing candles, as `[row_index, open_ts]`), so time is fully recoverable. About 20% fewer tokens.
* **Model:** the Model selector in the panel (or `SWING_AI_MODEL` in `.env` as the default). A model picked in the panel is saved and wins over `.env`.
  Changing the model starts a new record: the model is stored on every decision.
* **Meter:** tokens per review are stored; the panel shows tokens today (and dollars if you set `SWING_AI_PRICE_IN`, `SWING_AI_PRICE_CACHED`,
  `SWING_AI_PRICE_OUT` in USD per 1M tokens in `.env`). `swing_ai_report.py` prints them too.
