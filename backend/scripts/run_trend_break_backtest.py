"""Replay the Trend Break engine over local candles, one closed 1M bar at a time.

Same `evaluate()` as live; every call only sees candles closed by that instant.
Exit sim: fixed SL/TP from the FIRE entry, 1M bars, SL wins a same-bar tie.
Usage: python scripts/run_trend_break_backtest.py [--start TS] [--end TS] [--no-align]
"""
import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.market_data import database as db  # noqa: E402
from src.config import SYMBOL  # noqa: E402
from src.trend_break import evaluate, TrendBreakConfig  # noqa: E402
from src.trend_break.engine import TF_SEC  # noqa: E402

LIMITS = {"1d": 120, "4h": 200, "1h": 300, "15m": 300, "5m": 300, "1m": 700}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--no-align", action="store_true")
    a = ap.parse_args()
    cfg = TrendBreakConfig(require_master_alignment=not a.no_align)
    rows = {tf: db.get_candles(SYMBOL, tf, limit=100000) for tf in TF_SEC}
    opens = {tf: [c["ts"] for c in rows[tf]] for tf in rows}
    m1 = rows["1m"]
    trades, fired_setups, pos = [], set(), None
    for i, bar in enumerate(m1):
        now = bar["ts"] + 60
        if (a.start and now < a.start) or (a.end and now > a.end):
            continue
        if pos:
            hi, lo = float(bar["high"]), float(bar["low"])
            s = 1 if pos["side"] == "LONG" else -1
            hit_sl = lo <= pos["sl"] if s > 0 else hi >= pos["sl"]
            hit_tp = hi >= pos["tp"] if s > 0 else lo <= pos["tp"]
            if hit_sl or hit_tp:
                pos["exit"] = "SL" if hit_sl else "TP"
                pos["r"] = -1.0 if hit_sl else cfg.tp_atr / cfg.sl_atr
                trades.append(pos)
                pos = None
            continue
        win = {}
        for tf, lim in LIMITS.items():
            end = bisect.bisect_right(opens[tf], now - TF_SEC[tf])
            win[tf] = rows[tf][max(0, end - lim):end]
        d = evaluate(win, config=cfg, now_ts=now)
        if d.fire and d.trend_break_ts not in fired_setups:
            fired_setups.add(d.trend_break_ts)
            pos = {"ts": now, "side": d.direction, "entry": d.entry, "sl": d.sl, "tp": d.tp,
                   "break_ts": d.trend_break_ts, "conf": d.confidence}
    wins = sum(1 for t in trades if t["exit"] == "TP")
    print(f"trades={len(trades)} wins={wins} losses={len(trades)-wins} "
          f"R={sum(t['r'] for t in trades):.1f} open={'yes' if pos else 'no'}")
    for t in trades:
        print(t)


if __name__ == "__main__":
    main()
