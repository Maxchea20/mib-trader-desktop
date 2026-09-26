# MiB Trader Desktop

Python engine is the trading source of truth. The window is Tauri (not Chrome).

## Daily use (dev)

Two terminals. **One engine only.**

```
# 1) engine
cd backend
.\.venv\Scripts\python.exe run_server.py

# 2) UI in Chrome (old way) OR the desktop window
cd frontend
npm.cmd start
```

Desktop window instead of Chrome (engine already running on 8811):

```
cd desktop
npm install
npm run dev
```

The host will see 8811 is already up and will **not** start a second engine.
Window X hides to tray. Tray → **Exit MiB Trader** is the only full quit.

## Packaged Windows app

See `desktop/BUILD_INSTRUCTIONS.md`.

Never run packaged `MIB Trader.exe` and `run_server.py` at the same time.

## Do not change

Brain / S1-S2 / autotrader execution live in Python. The desktop host only
opens a window and manages the engine process.

Live path: forming 15m BOS/CHoCH → S1/S2 → C 1m close → FIRE → Isolated.
Hunt C-FI is historical research only (`docs/HUNT_*`, `scripts/run_hunt_*`).
