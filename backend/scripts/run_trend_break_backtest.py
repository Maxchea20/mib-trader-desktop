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

# furthest stage a break reached (EXPIRED is only the fallback once nothing else happened)
RANK = {"EXPIRED": 0, "MASTER_DIRECTION": 1, "TREND_BREAK_1H": 2, "BREAK_15M_CONFIRMATION": 3,
        "BREAK_5M_CONFIRMATION": 4, "WAIT_PULLBACK": 5, "1M_ENTRY_OPPORTUNITY": 6,
        "CANCELLED": 7, "DONE": 8, "FIRE": 9}
LIMITS = {"1d": 400, "4h": 600, "1h": 600, "15m": 400, "5m": 300, "1m": 700}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--no-align", action="store_true")
    ap.add_argument("--no-master", action="store_true", help="1D/4H/1H are reported but never block a setup")
    ap.add_argument("--master-length", type=int, default=14, help="swing lookback for 1D/4H/1H structure")
    ap.add_argument("--master-lengths", default="", help="per-timeframe override, e.g. 1d=5,4h=8,1h=8")
    ap.add_argument("--min-confidence", type=float, default=None, help="override engine min_confidence (default 0.30)")
    ap.add_argument("--diagnose", action="store_true", help="path diagnostic for every fired trade (analysis only)")
    ap.add_argument("--diag-csv", default="trend_break_diagnostic.csv")
    ap.add_argument("--blocked-csv", help="write every non-fired setup to this CSV")
    ap.add_argument("--setup-tf", default="1h", choices=["1h", "15m"])
    a = ap.parse_args()
    cfg = TrendBreakConfig(require_master_alignment=not a.no_align, setup_tf=a.setup_tf,
                            use_master_filter=not a.no_master,
                            master_length=a.master_length,
                            **({'min_confidence': a.min_confidence} if a.min_confidence is not None else {}),
                            master_lengths={k.strip(): int(v) for k, v in (x.split('=') for x in a.master_lengths.split(',') if x)})
    rows = {tf: db.get_candles(SYMBOL, tf, limit=100000) for tf in TF_SEC}
    opens = {tf: [c["ts"] for c in rows[tf]] for tf in rows}
    m1 = rows["1m"]
    trades, fired_setups, pos = [], set(), None
    last_state = {}   # break_ts -> (direction, last setup_state seen)
    skipped_in_pos = set()
    for i, bar in enumerate(m1):
        now = bar["ts"] + 60
        if (a.start and now < a.start) or (a.end and now > a.end):
            continue
        if pos:
            hi, lo = float(bar["high"]), float(bar["low"])
            s = 1 if pos["side"] == "LONG" else -1
            fav = (hi - pos["entry"]) if s > 0 else (pos["entry"] - lo)
            adv = (pos["entry"] - lo) if s > 0 else (hi - pos["entry"])
            pos["mfe"] = max(pos["mfe"], fav / pos["_risk"])
            pos["mae"] = max(pos["mae"], adv / pos["_risk"])
            hit_sl = lo <= pos["sl"] if s > 0 else hi >= pos["sl"]
            hit_tp = hi >= pos["tp"] if s > 0 else lo <= pos["tp"]
            if hit_sl or hit_tp:
                pos["exit"] = "SL" if hit_sl else "TP"
                pos["r"] = -1.0 if hit_sl else cfg.tp_atr / cfg.sl_atr
                trades.append(pos)
                pos = None
            skipped_in_pos.add(bar["ts"] // 3600)
            continue
        win = {}
        for tf, lim in LIMITS.items():
            end = bisect.bisect_right(opens[tf], now - TF_SEC[tf])
            win[tf] = rows[tf][max(0, end - lim):end]
        d = evaluate(win, config=cfg, now_ts=now)
        if d.break_detected:
            cur = last_state.get(d.trend_break_ts)
            if cur is None or RANK.get(d.setup_state, 0) >= RANK.get(cur[1], 0):
                last_state[d.trend_break_ts] = (d.direction, d.setup_state, d.reason,
                                                d.master_1d_direction, d.master_4h_direction, d.master_1h_direction)
        if d.fire and d.trend_break_ts not in fired_setups:
            fired_setups.add(d.trend_break_ts)
            pos = {"ts": now, "side": d.direction, "entry": d.entry, "sl": d.sl, "tp": d.tp,
                   "break_ts": d.trend_break_ts, "conf": d.confidence,
                   "q15": (d.m15_quality or {}).get("label"), "q5": (d.m5_quality or {}).get("label"),
                   "struct": (d.structure_confidence or {}).get("state"),
                   "align": d.master_alignment, "stop_pct": abs(d.entry - d.sl) / d.entry * 100,
                   "mfe": 0.0, "mae": 0.0, "_risk": abs(d.entry - d.sl), "atr": d.atr, "i": i}
    if a.diagnose:
        from src.trend_break.diagnostics import report
        print(report(trades + ([pos] if pos else []), m1, a.diag_csv))
        return
    wins = sum(1 for t in trades if t["exit"] == "TP")
    print(f"trades={len(trades)} wins={wins} losses={len(trades)-wins} "
          f"R={sum(t['r'] for t in trades):.1f} open={'yes' if pos else 'no'}")
    from collections import Counter
    print(f"\nFUNNEL: distinct setup-timeframe breaks seen = {len(last_state)}")
    for st, n in Counter('FIRED' if k in fired_setups else v[1] for k, v in last_state.items()).most_common():
        print(f"  {st:26s} {n}")
    print("\nBLOCKED reasons (non-fired):")
    for r, n in Counter(v[2][:70] for k, v in last_state.items() if k not in fired_setups).most_common(8):
        print(f"  {n:4d}  {r}")
    blocked = sorted((k, v) for k, v in last_state.items()
                     if k not in fired_setups and v[1] == "MASTER_DIRECTION")
    print(f"\nBLOCKED BY MASTER DIRECTION: {len(blocked)} of {len(last_state)} breaks")
    print(f"  {'break side':10s} {'1D':8s} {'4H':8s} {'1H':8s} {'count':>5s}")
    for (sd, m1d, m4h, m1h), n in Counter((v[0], v[3], v[4], v[5]) for _, v in blocked).most_common():
        print(f"  {sd:10s} {m1d:8s} {m4h:8s} {m1h:8s} {n:5d}")
    if a.blocked_csv:
        import csv, datetime
        with open(a.blocked_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["break_ts", "break_time_utc", "side", "final_state", "1d", "4h", "1h", "reason"])
            for k, v in sorted(last_state.items()):
                if k in fired_setups:
                    continue
                w.writerow([k, datetime.datetime.utcfromtimestamp(k).isoformat(), v[0], v[1],
                            v[3], v[4], v[5], v[2]])
        print(f"wrote {a.blocked_csv}")
    def sweep():
        """Same entries and same 1.5 ATR stop; vary only the target.  Each trade is
        simulated independently on 1M bars (ignores the one-position rule)."""
        print("\nTARGET SWEEP (stop fixed at %.1f ATR; every fired entry simulated on its own)" % cfg.sl_atr)
        print(f"  {'TP (ATR)':>9s} {'R at win':>9s} {'n':>4s} {'win%':>6s} {'R total':>8s} {'R/trade':>8s}")
        for tpm in (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0):
            tot = wins = 0
            for t in trades:
                sg = 1 if t["side"] == "LONG" else -1
                sl = t["entry"] - sg * cfg.sl_atr * t["atr"]
                tp = t["entry"] + sg * tpm * t["atr"]
                res = None
                for c in m1[t["i"] + 1:t["i"] + 4000]:
                    h, l = float(c["high"]), float(c["low"])
                    hs = l <= sl if sg > 0 else h >= sl
                    ht = h >= tp if sg > 0 else l <= tp
                    if hs:
                        res = -1.0
                        break
                    if ht:
                        res = tpm / cfg.sl_atr
                        wins += 1
                        break
                tot += res if res is not None else 0.0
            n = len(trades)
            print(f"  {tpm:9.1f} {tpm/cfg.sl_atr:9.2f} {n:4d} {100*wins/n:5.0f}% {tot:8.1f} {tot/n:8.2f}")

    def bucket(title, keyf):
        groups = {}
        for t in trades:
            groups.setdefault(keyf(t), []).append(t)
        print(f"\n{title}")
        print(f"  {'group':22s} {'n':>4s} {'win%':>6s} {'R':>7s} {'avgMFE':>7s} {'avgMAE':>7s}")
        for k in sorted(groups, key=str):
            g = groups[k]
            w = sum(1 for t in g if t["exit"] == "TP")
            print(f"  {str(k):22s} {len(g):4d} {100*w/len(g):5.0f}% {sum(t['r'] for t in g):7.1f} "
                  f"{sum(t['mfe'] for t in g)/len(g):7.2f} {sum(t['mae'] for t in g)/len(g):7.2f}")
    if trades:
        sweep()
        bucket("BY SIDE", lambda t: t["side"])
        bucket("BY CONFIDENCE", lambda t: "<0.5" if t["conf"] < .5 else "0.5-0.7" if t["conf"] < .7 else "0.7-0.85" if t["conf"] < .85 else ">=0.85")
        bucket("BY 15M/setup QUALITY", lambda t: t["q15"])
        bucket("BY 5M QUALITY", lambda t: t["q5"])
        bucket("BY CHoCH/BOS GAUGE", lambda t: t["struct"])
        bucket("BY MASTER ALIGNMENT", lambda t: t["align"])
        bucket("BY STOP SIZE (% of price)", lambda t: "<0.15%" if t["stop_pct"] < .15 else "0.15-0.3%" if t["stop_pct"] < .3 else "0.3-0.5%" if t["stop_pct"] < .5 else ">=0.5%")
        bucket("BY HOUR UTC (4h blocks)", lambda t: f"{(t['ts'] % 86400)//14400*4:02d}-{(t['ts'] % 86400)//14400*4+4:02d}h")
    print()
    for t in trades:
        print(t)


if __name__ == "__main__":
    main()
