#!/usr/bin/env python3
"""READ-ONLY study: would a LIMIT entry at the broken 5m line (the retest)
beat today's MARKET entry at the first 1m close through that line?

Replays the CURRENT live engine (evaluate_s1) on the local candle DB and,
for every FIRE, simulates the same trade three ways:

  A  today: market entry at the 1m close that confirmed C
  B  limit at the 5m line S1 was watching, valid until the current 15m closes
  C  limit at the same 5m line, valid for 5 minutes only

Same signal, same SL 1.5 x ATR15 / TP 2.5 x ATR15 (measured from each
version's own fill price), same exits. Nothing is changed in the engine.

  python scripts/s1_entry_study.py                     # all 1m data
  python scripts/s1_entry_study.py --start 2026-08-14 --end 2026-09-24
  python scripts/s1_entry_study.py --maker-fee 0.0     # if your MEXC maker fee is 0

HOW THE REPLAY MATCHES LIVE
  At each moment it gives evaluate_s1 only candles CLOSED by then, with the
  live lookbacks (15m 320, 5m 960, 1m 400) and the same aux observers.
  The engine's inputs only change when a 5m candle closes, except inside a
  C-watch where each 1m close matters -- so it evaluates on every 5m close
  and on every 1m close while a C-watch is running (identical decisions to
  the live 5-second poll, far faster). Live-eligible FIRE = FIRE with
  entry/stop/target, 4h weather allows the side, not already fired this 5m.

FILL RULES FOR THE LIMIT (conservative)
  * The order is placed when the confirming 1m candle closes.
  * It fills only if a LATER 1m candle trades THROUGH the limit by at least
    one tick (0.1) -- touching is not assumed to fill (queue position).
  * If the fill candle also reaches the stop, the stop is taken. TP can only
    be hit from the candle after the fill.
  * Not filled before expiry = no trade (0 P&L), counted as a missed FIRE.

FEES (per side, % of price)
  A: taker in + taker out.  B/C: maker in (--maker-fee) + taker out.
  Also reported with maker = taker, so you can see how much is price vs fee.

OUTPUT: comparison table for ALL FIREs, both time halves, and the live-like
sequence (one position at a time, 15-min cooldown after an SL); plus how
many winners B/C missed because price never came back.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TF = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
LOOKBACK = {"15m": 320, "5m": 960, "1m": 400, "1h": 400, "4h": 300}
TICK = 0.1
SL_COOLDOWN_S = 3 * 300


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def to_ts(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def load(db, symbol, tf):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts",
                     (symbol, tf)).fetchall()
    c.close()
    return [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
             "close": float(r[4]), "volume": float(r[5] or 0.0)} for r in rows]


class Series:
    def __init__(self, rows, tf):
        self.rows, self.step, self.ts = rows, TF[tf], [r["ts"] for r in rows]

    def closed_upto(self, now, n):
        k = bisect.bisect_right(self.ts, now - self.step)
        return self.rows[max(0, k - n):k]

    def index_at_or_after(self, t):
        return bisect.bisect_left(self.ts, t)


# ------------------------------------------------------------------ replay

def replay(S, start, end):
    from src.brain import s1_engine
    from src.brain.s1_engine import evaluate_s1, reset_s1_state
    from src.brain.weather import classify, side_allowed
    from src.fair_value_gap.observe import observe as obs_fvg
    from src.momentum.observe import observe as obs_mom
    from src.volume.observe import observe as obs_vol
    from src.support_resistance.observe import observe as obs_sr

    reset_s1_state()
    aux_cache, wx_cache = {}, {}
    fires = []
    last_fired_5m = None
    t = start - start % 300 + 300
    in_watch = False
    n_eval, t0 = 0, time.time()
    total = (end - t) // 300
    while t <= end:
        c15 = S["15m"].closed_upto(t, LOOKBACK["15m"])
        c5 = S["5m"].closed_upto(t, LOOKBACK["5m"])
        c1 = S["1m"].closed_upto(t, LOOKBACK["1m"])
        if len(c15) >= 80 and len(c5) >= 80 and c1:
            k15 = c15[-1]["ts"]
            if k15 not in aux_cache:
                aux_cache.clear()
                try:
                    aux_cache[k15] = {"mom": obs_mom(c15, "15m"), "vol": obs_vol(c15, "15m"),
                                      "sr": obs_sr(c15, "15m"), "fvg": obs_fvg(c15, "15m")}
                except Exception:
                    aux_cache[k15] = {}
            watch_before = dict((s1_engine._STATE.get("c_watch") or {}))
            try:
                out = evaluate_s1(c15, c5[-1], candles_5m=c5, candles_1m=c1, aux=aux_cache[k15])
            except Exception as e:
                out = {"action": "WAIT", "why_state": [f"error {e}"]}
            n_eval += 1
            in_watch = out.get("timing_state") == "C_WATCH"
            if out.get("action") == "FIRE" and c5[-1]["ts"] != last_fired_5m:
                c4 = S["4h"].closed_upto(t, LOOKBACK["4h"])
                c1h = S["1h"].closed_upto(t, LOOKBACK["1h"])
                kw = (c4[-1]["ts"] if c4 else None, c1h[-1]["ts"] if c1h else None)
                if kw not in wx_cache:
                    wx_cache.clear()
                    wx_cache[kw] = classify(c4, c1h) if c4 else {}
                flag = (wx_cache[kw] or {}).get("flag")
                side = out.get("direction")
                ok = out.get("entry") and out.get("stop") and out.get("target") and side in ("LONG", "SHORT")
                if ok and not (flag and not side_allowed(flag, side)):
                    last_fired_5m = c5[-1]["ts"]
                    fires.append({
                        "t": t, "side": side, "entry": float(c1[-1]["close"]),  # market fill at FIRE time
                        "c_price": float(out["entry"]),  # engine's C 1m close (can be minutes old)
                        "atr": float(out.get("atr_15m") or 0.0),
                        "line": watch_before.get("structural_level"),
                        "path": out.get("timing"), "thesis_level": out.get("thesis_level"),
                        "slot": out.get("slot"),
                    })
        if n_eval and n_eval % 2000 == 0:
            done = (t - start) / max(1, end - start)
            el = time.time() - t0
            print(f"  replay {iso(t)}  fires={len(fires)}  eta {el / max(done, 1e-6) * (1 - done) / 60:.0f} min", flush=True)
        t += 60 if in_watch else (300 - t % 300 if t % 300 else 300)
    return fires


# ------------------------------------------------------------------ trade simulation

def run_trade(side, fill_px, A, rows, i0, horizon_end, fee_in, fee_out, fill_bar_sl_only=False):
    """Bracket from fill_px. rows[i0] is the first candle checked.
    If fill_bar_sl_only, candle i0 is the fill candle: only SL can hit on it."""
    long_ = side == "LONG"
    sl = fill_px - 1.5 * A if long_ else fill_px + 1.5 * A
    tp = fill_px + 2.5 * A if long_ else fill_px - 2.5 * A
    fees = (fee_in + fee_out) / 100 * fill_px
    i = i0
    last = None
    mfe = 0.0
    while i < len(rows) and rows[i]["ts"] < horizon_end:
        c = rows[i]
        hit_sl = c["low"] <= sl if long_ else c["high"] >= sl
        hit_tp = (c["high"] >= tp if long_ else c["low"] <= tp) and not (fill_bar_sl_only and i == i0)
        fav = (c["high"] - fill_px) if long_ else (fill_px - c["low"])
        if hit_sl:
            return {"out": "SL", "pnl": -1.5 * A - fees, "mfe": mfe, "exit_ts": c["ts"] + 60}
        if hit_tp:
            return {"out": "TP", "pnl": 2.5 * A - fees, "mfe": max(mfe, fav), "exit_ts": c["ts"] + 60}
        mfe = max(mfe, fav)
        last = c["close"]
        i += 1
    if last is None:
        return None
    move = (last - fill_px) if long_ else (fill_px - last)
    return {"out": "TIMEOUT", "pnl": move - fees, "mfe": mfe, "exit_ts": rows[i - 1]["ts"] + 60}


def simulate(fires, one_min, horizon_s, taker, maker):
    ts = one_min.ts
    rows = one_min.rows
    res = []
    for f in fires:
        A = f["atr"]
        side, long_ = f["side"], f["side"] == "LONG"
        i0 = bisect.bisect_left(ts, f["t"])  # first 1m candle opening at/after FIRE time
        hz = f["t"] + horizon_s
        r = {"A": None, "A_same": None, "B": None, "C": None, "B_eq": None, "C_eq": None}
        if A <= 0 or i0 >= len(rows):
            res.append(r)
            continue
        r["A"] = run_trade(side, f["entry"], A, rows, i0, hz, taker, taker)
        line = f.get("line")
        for name, expiry in (("B", f["t"] - f["t"] % 900 + 900), ("C", f["t"] + 300)):
            if line is None:
                continue
            limit = float(line)
            # a limit that is already on the wrong side of the market would be a market order: skip it
            if (long_ and limit >= f["entry"]) or ((not long_) and limit <= f["entry"]):
                r[name] = {"out": "NOT_LIMIT", "pnl": None}
                continue
            j = i0
            filled = None
            while j < len(rows) and rows[j]["ts"] < expiry:
                c = rows[j]
                if (long_ and c["low"] <= limit - TICK) or ((not long_) and c["high"] >= limit + TICK):
                    filled = j
                    break
                j += 1
            if filled is None:
                r[name] = {"out": "NO_FILL", "pnl": 0.0}
                r[name + "_eq"] = {"out": "NO_FILL", "pnl": 0.0}
                continue
            t_res = run_trade(side, limit, A, rows, filled, hz, maker, taker, fill_bar_sl_only=True)
            eq = run_trade(side, limit, A, rows, filled, hz, taker, taker, fill_bar_sl_only=True)
            if t_res:
                t_res["improve_atr"] = abs(f["entry"] - limit) / A
                t_res["fill_min"] = (rows[filled]["ts"] - f["t"]) // 60 + 1
            r[name], r[name + "_eq"] = t_res, eq
        # A scored on exactly the FIREs where a limit was possible (like-for-like with B/C)
        if r.get("B") and r["B"].get("out") != "NOT_LIMIT":
            r["A_same"] = r["A"]
        res.append(r)
    return res


# ------------------------------------------------------------------ stats

def _med(v):
    v = [x for x in v if x is not None]
    return st.median(v) if v else None


def stats(fires, res, key):
    rows = [(f, r[key]) for f, r in zip(fires, res) if r.get(key) is not None and r[key].get("pnl") is not None]
    if not rows:
        return None
    pct = [100 * x["pnl"] / f["entry"] for f, x in rows]
    filled = [(f, x) for f, x in rows if x["out"] != "NO_FILL"]
    fp = [100 * x["pnl"] / f["entry"] for f, x in filled]
    eq = pk = dd = 0.0
    for v in pct:
        eq += v
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
    gp, gl = sum(v for v in fp if v > 0), -sum(v for v in fp if v < 0)
    return {
        "fires": len(rows), "filled": len(filled),
        "tp": sum(x["out"] == "TP" for _, x in filled), "sl": sum(x["out"] == "SL" for _, x in filled),
        "to": sum(x["out"] == "TIMEOUT" for _, x in filled),
        "win": 100 * sum(v > 0 for v in fp) / len(fp) if fp else 0.0,
        "net": sum(pct), "exp_fire": st.mean(pct), "exp_trade": st.mean(fp) if fp else 0.0,
        "pf": gp / gl if gl else float("inf"), "dd": dd,
        "improve": _med([x.get("improve_atr") for _, x in filled]),
        "fill_min": _med([x.get("fill_min") for _, x in filled]),
    }


def table(title, fires, res, L):
    L.append(f"\n{title}")
    h = (f"{'version':<30}{'FIREs':>6}{'filled':>7}{'fill%':>7}{'TP':>5}{'SL':>5}{'T/O':>5}{'win%':>7}"
         f"{'net%':>8}{'exp/FIRE%':>10}{'exp/trade%':>11}{'PF':>6}{'maxDD%':>8}{'better ATR':>11}{'fill min':>9}")
    L.append(h)
    L.append("-" * len(h))
    for key, name in (("A", "A market at 1m close (today)"), ("A_same", "A on the same FIREs as B/C"),
                      ("B", "B limit at 5m line, to 15m end"),
                      ("C", "C limit at 5m line, 5 min"), ("B_eq", "  B with maker fee = taker"),
                      ("C_eq", "  C with maker fee = taker")):
        s = stats(fires, res, key)
        if not s:
            L.append(f"{name:<30} no data")
            continue
        L.append(f"{name:<30}{s['fires']:>6}{s['filled']:>7}{100 * s['filled'] / s['fires']:>6.1f}%{s['tp']:>5}{s['sl']:>5}"
                 f"{s['to']:>5}{s['win']:>7.1f}{s['net']:>8.2f}{s['exp_fire']:>10.3f}{s['exp_trade']:>11.3f}"
                 f"{s['pf']:>6.2f}{s['dd']:>8.2f}{('%.2f' % s['improve']) if s['improve'] is not None else '-':>11}"
                 f"{('%.0f' % s['fill_min']) if s['fill_min'] is not None else '-':>9}")


def missed(fires, res, L):
    L.append("\nWHAT THE LIMIT GIVES UP / GAINS vs A (same FIREs)")
    for key in ("B", "C"):
        pairs = [(r["A"], r[key]) for r in res if r.get("A") and r.get(key) and r[key].get("pnl") is not None]
        nf = [a for a, b in pairs if b["out"] == "NO_FILL"]
        fl = [(a, b) for a, b in pairs if b["out"] != "NO_FILL"]
        L.append(f"  {key}: not filled {len(nf)}  -> of these A would have WON {sum(a['pnl'] > 0 for a in nf)}, "
                 f"LOST {sum(a['pnl'] <= 0 for a in nf)}  (A's P&L on them: "
                 f"{sum(a['pnl'] for a in nf):+.1f} price units)")
        L.append(f"     filled {len(fl)} -> A WIN->{key} LOSS {sum(a['pnl'] > 0 >= b['pnl'] for a, b in fl)}, "
                 f"A LOSS->{key} WIN {sum(a['pnl'] <= 0 < b['pnl'] for a, b in fl)}")
        nl = sum(1 for r in res if r.get(key) and r[key].get("out") == "NOT_LIMIT")
        if nl:
            L.append(f"     {nl} FIREs where the line was not behind the entry (limit would be a market order) are excluded")


def live_like(fires, res):
    """One position at a time + cooldown after SL, using A's exits to pick the
    entries; every version is then scored on exactly those entries."""
    keep, busy, cool = [], 0, 0
    for i, (f, r) in enumerate(zip(fires, res)):
        a = r.get("A")
        if not a or f["t"] < busy or f["t"] < cool:
            continue
        keep.append(i)
        busy = a["exit_ts"]
        cool = busy + SL_COOLDOWN_S if a["out"] == "SL" else 0
    return keep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(BACKEND / "market_data_clean.db"))
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--horizon-hours", type=float, default=72.0)
    ap.add_argument("--taker-fee", type=float, default=0.08, help="% per side (your real MEXC rate)")
    ap.add_argument("--maker-fee", type=float, default=0.01, help="% per side for the limit entry -- check your MEXC tier")
    ap.add_argument("--out", default="s1_entry_study.json")
    a = ap.parse_args()

    if not Path(a.db).is_file():
        sys.exit(f"Database not found: {a.db}")
    print(f"Loading candles from {a.db} (read-only) ...")
    S = {tf: Series(load(a.db, a.symbol, tf), tf) for tf in TF}
    for tf, s in S.items():
        print(f"  {tf:>3}: {len(s.rows):>7}  {iso(s.ts[0]) if s.ts else '-'} -> {iso(s.ts[-1]) if s.ts else '-'}")
    hz = int(a.horizon_hours * 3600)
    start = to_ts(a.start) if a.start else max(S["1m"].ts[0], S["5m"].ts[0]) + 3 * 86400
    end = to_ts(a.end) if a.end else S["1m"].ts[-1] - hz
    print(f"Replaying evaluate_s1 {iso(start)} -> {iso(end)} ...")
    fires = replay(S, start, end)
    print(f"FIREs (live-eligible): {len(fires)}   with a 5m line recorded: {sum(1 for f in fires if f['line'] is not None)}")
    if not fires:
        sys.exit("no FIREs")
    res = simulate(fires, S["1m"], hz, a.taker_fee, a.maker_fee)

    L = [f"S1 entry study  {iso(fires[0]['t'])} -> {iso(fires[-1]['t'])}   FIREs {len(fires)} "
         f"(LONG {sum(f['side'] == 'LONG' for f in fires)} / SHORT {sum(f['side'] == 'SHORT' for f in fires)})",
         f"SL 1.5 ATR / TP 2.5 ATR from each fill; taker {a.taker_fee}%/side, maker {a.maker_fee}%/side; "
         f"horizon {a.horizon_hours:g}h; limit fills need a trade-through by 1 tick",
         "exp/FIRE counts unfilled FIREs as 0 (the honest comparison); exp/trade is per filled trade."]
    table("== ALL FIREs ==", fires, res, L)
    half = fires[len(fires) // 2]["t"]
    for lab, cond in (("1st half", lambda f: f["t"] < half), ("2nd half", lambda f: f["t"] >= half)):
        idx = [i for i, f in enumerate(fires) if cond(f)]
        table(f"== {lab}: {iso(fires[idx[0]]['t'])} -> {iso(fires[idx[-1]]['t'])} ==",
              [fires[i] for i in idx], [res[i] for i in idx], L)
    ll = live_like(fires, res)
    table(f"== LIVE-like (one position at a time, A's exits pick the entries; n={len(ll)}) ==", [fires[i] for i in ll], [res[i] for i in ll], L)
    for p in sorted({f["path"] for f in fires}, key=str):
        idx = [i for i, f in enumerate(fires) if f["path"] == p]
        table(f"== path {p} (n={len(idx)}) ==", [fires[i] for i in idx], [res[i] for i in idx], L)
    gap = [abs(f["entry"] - f["c_price"]) / f["atr"] for f in fires if f["atr"]]
    if gap:
        L.append(f"\nEngine C price vs real market price at FIRE: median gap {st.median(gap):.2f} ATR "
                 f"(p90 {sorted(gap)[int(.9 * (len(gap) - 1))]:.2f}) -- live fills at market, so A uses the market price.")
    missed(fires, res, L)
    rep = "\n".join(L)
    print(rep)
    Path(a.out).write_text(json.dumps({"args": vars(a), "fires": [
        {**f, "time": iso(f["t"]), "results": r} for f, r in zip(fires, res)]}, indent=1, default=str))
    Path(a.out).with_suffix(".txt").write_text(rep, encoding="utf-8")
    print(f"\nWritten {a.out} and {Path(a.out).with_suffix('.txt')}")


if __name__ == "__main__":
    main()
