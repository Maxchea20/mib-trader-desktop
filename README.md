# MiB Trader Desktop

Python engine is the trading source of truth. The desktop window is Tauri.

## Architecture

Hunt is the single trading brain and the only FIRE authority.

```
Hunt thesis
   ├── S1     early forming-15M breakout → C → FIRE
   ├── S2     extension/pullback → C → FIRE
   └── SLOT3  confirmed closed-15M breakout → FIRE
                         |
                       ONE FIRE
                         |
                  autotrader execution
                         |
                    MEXC Isolated
```

S1, S2, SLOT3 and C never place orders themselves.

## Daily use

Engine:

```
cd backend
.\.venv\Scripts\python.exe run_server.py
```

Desktop development:

```
cd desktop
npm install
npm run dev
```

Do not run a packaged MIB Trader executable and `run_server.py` against the same engine at the same time.

## Important

- Market structure calculations use closed candles for historical/structural decisions.
- S1 uses the current forming 15M objective and current 5M sections.
- SLOT3 uses a confirmed closed 15M CHoCH/BOS and enters on the next 15M candle.
- S2 remains a separate extension/pullback timing path.
- C is 1M entry timing only.
- Lifecycle owns post-FIRE HOLD/EXIT management.
- Historical replay must validate the current Hunt architecture before performance conclusions are drawn.
