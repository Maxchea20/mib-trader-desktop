#!/usr/bin/env python3
"""Desktop hunt + weather + V1b lifecycle backtest.

From the backend folder:

  python scripts/run_hunt_lifecycle_backtest.py --help

  python scripts/run_hunt_lifecycle_backtest.py --start 2026-01-01 --end 2026-09-13 --sl 1 --tp 2.5 --capital 500 --risk 0.02 --weather --out hunt_jan_sep.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from src.brain.observation_hunt import evaluate_hunt, _fresh
from src.brain.lifecycle import position_from_fire, reevaluate, EXIT
from src.brain.weather import classify, side_allowed
from src.structure.observe import observe as obs_structure
from src.trend.observe import observe as obs_trend


def _ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _prefix(rows, ts):
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid]["ts"] <= ts:
            lo = mid + 1
        else:
            hi = mid
    return rows[:lo]


def load_klines_dir(d: Path):
    def one(name):
        p = d / name
        if not p.exists():
            raise FileNotFoundError(p)
        return json.loads(p.read_text())
    return one("btc_5m.json"), one("btc_15m.json"), one("btc_4h.json"), one("btc_1h.json")


def load_sqlite(limit: int):
    from src.market_data import database as db
    db.init_db()
    return (
        db.get_candles("BTC_USDT", "5m", limit=limit),
        db.get_candles("BTC_USDT", "15m", limit=limit),
        db.get_candles("BTC_USDT", "4h", limit=limit),
        db.get_candles("BTC_USDT", "1h", limit=limit),
    )


def summarize(trades, equity, max_dd, hits, stats, flags, start_ts, end_ts, args):
    def ss(side):
        s = [t for t in trades if t.get("side") == side]
        w = sum(1 for t in s if t["pnl"] > 0)
        return dict(n=len(s), w=w, l=len(s) - w, pnl=round(sum(t["pnl"] for t in s), 2))
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return dict(
        sl_tp=f"{args.sl}:{args.tp} ATR",
        capital=args.capital, risk=args.risk, weather=args.weather,
        window=f"{datetime.fromtimestamp(start_ts, timezone.utc).date()} to {datetime.fromtimestamp(end_ts, timezone.utc).date()}",
        n_trades=len(trades), wins=len(wins),
        win_rate=(len(wins) / len(trades) * 100) if trades else 0,
        pf=(gw / gl) if gl else None,
        equity_end=equity, pnl_usd=equity - args.capital,
        max_drawdown_pct=max_dd * 100,
        long=ss("LONG"), short=ss("SHORT"),
        hits=hits, stats=stats, weather_bars=dict(flags),
        blocked=stats["blocked"], trades=trades,
    )


def main():
    p = argparse.ArgumentParser(description="Hunt + V1b lifecycle backtest (desktop)")
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-09-13")
    p.add_argument("--sl", type=float, default=1.0)
    p.add_argument("--tp", type=float, default=2.0)
    p.add_argument("--capital", type=float, default=500.0)
    p.add_argument("--risk", type=float, default=0.02)
    p.add_argument("--weather", action="store_true")
    p.add_argument("--klines-dir", default="")
    p.add_argument("--sqlite-limit", type=int, default=200000)
    p.add_argument("--out", default="hunt_lifecycle_backtest.json")
    args = p.parse_args()

    if args.klines_dir:
        c5, c15, c4, c1h = load_klines_dir(Path(args.klines_dir))
        src = f"klines-dir {args.klines_dir}"
    else:
        c5, c15, c4, c1h = load_sqlite(args.sqlite_limit)
        src = "sqlite"
    if not c5 or not c15:
        print("No candles. Sync the app or pass --klines-dir.", file=sys.stderr)
        sys.exit(1)
    print(f"data={src} 5m={len(c5)} 15m={len(c15)} 4h={len(c4)} 1h={len(c1h)}", flush=True)

    start_ts, end_ts = _ts(args.start), _ts(args.end)
    i5 = {c["ts"]: i for i, c in enumerate(c5)}
    equity, peak, max_dd = args.capital, args.capital, 0.0
    trades, pos, wx_at = [], None, None
    stats = dict(fills=0, blocked=0, n15=0)
    hits, flags = {}, Counter()

    for j, bar15 in enumerate(c15):
        if bar15["ts"] < start_ts:
            continue
        if bar15["ts"] >= end_ts and pos is None:
            break
        stats["n15"] += 1
        w15 = c15[max(0, j + 1 - 320): j + 1]
        w4 = _prefix(c4, bar15["ts"])[-200:]
        w1h = _prefix(c1h, bar15["ts"])[-200:]
        wx = classify(w4, w1h)
        flags[wx["flag"]] += 1
        if stats["n15"] % 500 == 0:
            print(f"  n15={stats['n15']} fills={stats['fills']} eq={equity:.1f}", flush=True)
        h1_state = obs_trend(w1h, "1h").state if len(w1h) >= 60 else None
        st_ev = st_dir = None
        if len(w15) >= 60:
            st = obs_structure(w15, "15m")
            for e in _fresh(st, bar15["ts"]):
                if e.event_type in ("CHoCH", "CHOCH"):
                    st_ev, st_dir = e.event_type, e.direction
                    break
        if pos is not None:
            level_lost = (pos.side == "LONG" and bar15["close"] < pos.entry) or (
                pos.side == "SHORT" and bar15["close"] > pos.entry
            )
            i = i5.get(bar15["ts"])
            if i is not None:
                for b5 in c5[i:]:
                    if b5["ts"] >= bar15["ts"] + 900:
                        break
                    out = reevaluate(
                        pos, price=float(b5["close"]), high=float(b5["high"]), low=float(b5["low"]),
                        structure_event=st_ev, structure_dir=st_dir, trend_1h_state=h1_state,
                        level_lost=level_lost and b5["ts"] >= bar15["ts"] + 840, now_ts=b5["ts"],
                    )
                    if out["action"] != EXIT:
                        continue
                    px = out.get("exit_px") or float(b5["close"])
                    move = (px - pos.entry) / pos.entry
                    if pos.side == "SHORT":
                        move = -move
                    pnl = pos.qty * pos.entry * move
                    equity = max(0.0, equity + pnl)
                    peak = max(peak, equity)
                    max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
                    kind = out.get("exit_kind") or out.get("reason")
                    hits[kind] = hits.get(kind, 0) + 1
                    trades.append(dict(
                        ts=pos.opened_ts,
                        iso=datetime.fromtimestamp(pos.opened_ts, timezone.utc).isoformat(),
                        side=pos.side, entry=pos.entry, sl=pos.sl, tp=pos.tp, exit=px,
                        pnl=round(pnl, 2), hit=kind, weather=wx_at, equity=round(equity, 2),
                    ))
                    pos = None
                    break
            continue
        if equity <= 1 or j < 80 or bar15["ts"] >= end_ts:
            continue
        fill5 = c5[i5[bar15["ts"] + 840]] if (bar15["ts"] + 840) in i5 else bar15
        fire = evaluate_hunt(w15, fill5, candles_4h=w4)
        if fire.get("action") != "FIRE":
            continue
        side = fire.get("direction") or fire.get("side")
        if args.weather and not side_allowed(wx["flag"], side):
            stats["blocked"] += 1
            continue
        atr = float(fire.get("atr_15m") or 0) or abs(float(fire["entry"]) - float(fire["stop"]))
        entry = float(fire["entry"])
        fire["stop"] = entry - args.sl * atr if side == "LONG" else entry + args.sl * atr
        fire["target"] = entry + args.tp * atr if side == "LONG" else entry - args.tp * atr
        fire["atr_15m"] = atr
        risk = args.risk if fire.get("size") == "FULL" else args.risk * 0.5
        pos = position_from_fire(fire, trade_id=str(j), equity=equity, risk_pct=risk, opened_ts=bar15["ts"])
        wx_at = wx["flag"]
        stats["fills"] += 1

    snap = summarize(trades, equity, max_dd, hits, stats, flags, start_ts, end_ts, args)
    Path(args.out).write_text(json.dumps(snap, indent=2))
    print(json.dumps({k: snap[k] for k in snap if k != "trades"}, indent=2))
    print(f"wrote {args.out}  trades={len(trades)}", flush=True)


if __name__ == "__main__":
    main()
