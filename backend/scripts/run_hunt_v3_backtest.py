#!/usr/bin/env python3
"""Hunt V3 60-day backtest: live 15m / first three 5ms. SL:TP = 1.5:2.5.

  python scripts/run_hunt_v3_backtest.py
  python scripts/run_hunt_v3_backtest.py --start 2026-07-16 --end 2026-09-14
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from src.brain.observation_hunt_v3 import evaluate_hunt_v3, SL_ATR, TP_ATR, HUNT_VERSION_V3
from src.brain.lifecycle import position_from_fire, reevaluate, EXIT
from src.contract import LONG, SHORT


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


_MEXC_IV = {"5m": "Min5", "15m": "Min15", "1h": "Min60", "4h": "Hour4"}
_MEXC_SEC = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def fetch_mexc(symbol: str, interval: str, start_s: int, end_s: int):
    iv = _MEXC_IV[interval]
    step = _MEXC_SEC[interval] * 2000
    out = []
    cur = start_s
    while cur < end_s:
        chunk_end = min(end_s, cur + step)
        url = (
            f"https://api.mexc.com/api/v1/contract/kline/{symbol}"
            f"?interval={iv}&start={cur}&end={chunk_end}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "mib-hunt-v3"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
        data = (payload or {}).get("data") or {}
        times = data.get("time") or []
        if not times:
            cur = chunk_end
            continue
        vol = data.get("vol") or data.get("volume") or [0] * len(times)
        for i, t in enumerate(times):
            out.append({
                "ts": int(t),
                "open": float(data["open"][i]),
                "high": float(data["high"][i]),
                "low": float(data["low"][i]),
                "close": float(data["close"][i]),
                "volume": float(vol[i]),
            })
        cur = chunk_end
        time.sleep(0.12)
    seen = {r["ts"]: r for r in out}
    rows = [seen[t] for t in sorted(seen)]
    print(f"  {interval}: {len(rows)} bars", flush=True)
    return rows


def slot_of(ts5: int) -> int:
    return int(((ts5 % 900) // 300))


def parent_open(ts5: int) -> int:
    return ts5 - (ts5 % 900)


def run(c5, c15, start_ts, end_ts, capital, risk, sl_atr, tp_atr):
    i15 = {c["ts"]: i for i, c in enumerate(c15)}
    equity, peak, max_dd = capital, capital, 0.0
    trades, pos = [], None
    stats = {"n5": 0, "fills": 0, "blocked": 0, "by_path": {}}

    for j, b5 in enumerate(c5):
        ts = int(b5["ts"])
        if ts < start_ts or ts >= end_ts:
            continue
        stats["n5"] += 1
        po = parent_open(ts)

        if pos is not None:
            out = reevaluate(
                pos, price=float(b5["close"]), high=float(b5["high"]), low=float(b5["low"]),
                level_lost=False, now_ts=ts,
            )
            if out["action"] == EXIT:
                px = float(out.get("exit_px") or b5["close"])
                move = (px - pos.entry) / pos.entry
                if pos.side == SHORT:
                    move = -move
                pnl = pos.qty * pos.entry * move
                equity = max(0.0, equity + pnl)
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
                kind = out.get("exit_kind") or out.get("reason")
                trades.append(dict(
                    ts=pos.opened_ts, iso=_iso(pos.opened_ts),
                    side=pos.side, entry=pos.entry, sl=pos.sl, tp=pos.tp,
                    exit=px, pnl=round(pnl, 2), hit=kind,
                    slot=getattr(pos, "_slot", None), path=getattr(pos, "_path", None),
                    equity=round(equity, 2),
                ))
                pos = None

        if pos is not None or equity <= 1:
            continue

        idx = i15.get(po)
        closed = c15[:idx] if idx is not None else [c for c in c15 if int(c["ts"]) < po]
        if len(closed) < 20:
            continue
        live = [c for c in c5[max(0, j - 2): j + 1] if parent_open(int(c["ts"])) == po]
        if not live or live[-1]["ts"] != ts:
            live = [b5]

        fire = evaluate_hunt_v3(closed, live)
        if fire.get("action") != "FIRE":
            continue
        side = fire["direction"]
        atr = float(fire.get("atr_15m") or 0)
        entry = float(b5["close"])
        fire["entry"] = entry
        fire["stop"] = entry - sl_atr * atr if side == LONG else entry + sl_atr * atr
        fire["target"] = entry + tp_atr * atr if side == LONG else entry - tp_atr * atr
        fire["atr_15m"] = atr
        pos = position_from_fire(fire, trade_id=str(ts), equity=equity, risk_pct=risk, opened_ts=ts)
        pos._slot = fire.get("hunt", {}).get("slot")
        pos._path = fire.get("hunt", {}).get("m5_path")
        stats["fills"] += 1
        stats["by_path"][pos._path] = stats["by_path"].get(pos._path, 0) + 1

    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)

    def ss(side):
        s = [t for t in trades if t["side"] == side]
        w = sum(1 for t in s if t["pnl"] > 0)
        return dict(n=len(s), w=w, l=len(s) - w, pnl=round(sum(t["pnl"] for t in s), 2))

    return dict(
        version=HUNT_VERSION_V3, sl_tp=f"{sl_atr}:{tp_atr} ATR",
        capital=capital, risk=risk,
        window=f"{_iso(start_ts)[:10]} to {_iso(end_ts)[:10]}",
        n_trades=len(trades), wins=len(wins),
        win_rate=round((len(wins) / len(trades) * 100), 2) if trades else 0,
        pf=round(gw / gl, 3) if gl else None,
        equity_end=round(equity, 2), pnl_usd=round(equity - capital, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        long=ss(LONG), short=ss(SHORT), stats=stats, trades=trades,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-07-16")
    p.add_argument("--end", default="2026-09-14")
    p.add_argument("--symbol", default="BTC_USDT")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--sl", type=float, default=SL_ATR)
    p.add_argument("--tp", type=float, default=TP_ATR)
    p.add_argument("--out", default="hunt_v3_60d.json")
    args = p.parse_args()
    start_ts, end_ts = _ts(args.start), _ts(args.end)
    pad = 20 * 86400
    print(f"fetch {args.symbol} 5m+15m {_iso(start_ts)} → {_iso(end_ts)}", flush=True)
    c5 = fetch_mexc(args.symbol, "5m", start_ts - pad, end_ts)
    c15 = fetch_mexc(args.symbol, "15m", start_ts - pad, end_ts)
    print(f"5m={len(c5)} 15m={len(c15)}", flush=True)
    snap = run(c5, c15, start_ts, end_ts, args.capital, args.risk, args.sl, args.tp)
    Path(args.out).write_text(json.dumps(snap, indent=2))
    print(json.dumps({k: snap[k] for k in snap if k != "trades"}, indent=2))
    print(f"wrote {args.out} trades={snap['n_trades']}", flush=True)


if __name__ == "__main__":
    main()
