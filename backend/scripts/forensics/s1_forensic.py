#!/usr/bin/env python3
"""TEMPORARY, READ-ONLY forensic: the CURRENT S1 engine (src/brain/s1_engine.py, evaluate_s1)
tested with exactly the Hunt C-FI forensic methodology (hunt_rearm_forensic.py).

Nothing is changed: S1/S2, execution, SL/TP and fees are the production code / the same
rules as the Hunt test. This script only replays and scores.

  cd backend
  python scripts/forensics/s1_forensic.py

SAME AS THE HUNT FORENSIC
  * Same DB (market_data_clean.db), same default date range (first 1m + 3 days -> last 1m - 72h),
    same lookbacks (15m 320, 5m 959, 1m 400, 4h 300, 1h 400), same aux mom/vol/sr/fvg on the 15m.
  * Polled at every 5m close and every 1m close while C_WATCH is running; each moment is
    polled until the engine state stops changing (= live 5 s poll on unchanged candles).
  * Live-eligible FIRE as the CURRENT loop takes it: entry/stop/target present, 4h weather
    allows the side, not already fired this 5m.
  * Execution = _open_live_from_s1: market fill at the last closed 1m close, SL/TP distances
    |entry-stop| / |target-entry| from S1 re-anchored on the fill; 1m bracket, SL first,
    72h horizon, taker 0.08 %/side both legs (or your MEXC history / --taker-fee).
  * LIVE-like = one position at a time + 3x5m cooldown after an SL.
  * Same statistics, same CSV columns (book = S1, kind = S1).

S1 has no fresh / re-arm gates, so gate is empty; the breakdown is by timing path (S1 / S2)
and 5m slot. Delay = FIRE time - close of the 15m candle that printed the thesis break.
S1 reads its thesis from the FORMING 15m candle, so a negative delay means S1 fired before
that 15m candle closed.

At the end, if hunt_rearm_forensic.csv / hunt_rearm_forensic_3d98c41.csv are in the working
folder, it prints Hunt FRESH vs S1 side by side (Hunt fresh ALL = book A fresh FIREs;
Hunt fresh LIVE-like = book B, which trades only fresh FIREs one at a time).
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics as st
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hunt_rearm_forensic as H  # noqa: E402  (same loader, execution, stats, CSV as the Hunt test)
from hunt_rearm_forensic import (  # noqa: E402
    BACKEND, LOOKBACK, MAX_POLLS, Series, load, iso, to_ts, fee_rates_from_history, TF,
    simulate, summarize, write_csv, _n,
)

HDR = (f"{'':<22}{'FIREs':>6}{'S1':>6}{'S2':>6}{'TP':>5}{'SL':>5}{'T/O':>5}{'W':>5}{'L':>5}{'win%':>6}"
       f"{'net%':>8}{'exp%':>7}{'netR':>8}{'PF':>6}{'maxDD%':>7}{'maxCL':>6}{'dly avg':>8}{'med':>6}{'max':>7}")


def row(name, fs):
    s = summarize(fs)
    if not s:
        return f"{name:<22} no trades"
    fs = [f for f in fs if f["pnl_pct"] is not None]
    n1 = sum(f.get("timing") == "S1" for f in fs)
    n2 = sum(f.get("timing") == "S2" for f in fs)
    return (f"{name:<22}{s['n']:>6}{n1:>6}{n2:>6}{s['tp']:>5}{s['sl']:>5}{s['to']:>5}{s['wins']:>5}"
            f"{s['losses']:>5}{s['win']:>6.1f}{s['net']:>8.2f}{s['exp']:>7.3f}{s['netR']:>8.1f}{s['pf']:>6.2f}"
            f"{s['dd']:>7.2f}{s['cl']:>6}{_n(s['d_avg'], '.0f'):>8}{_n(s['d_med'], '.0f'):>6}{_n(s['d_max'], '.0f'):>7}")


# ------------------------------------------------------------------ replay (current S1, unchanged)

def _memo(mod, name, keyf):
    """Memoise a PURE helper by its input candles (speed only, same results)."""
    raw = getattr(mod, name)
    cache = {}

    def f(rows, *a, **k):
        if not rows or a or k:
            return raw(rows, *a, **k)
        key = keyf(rows)
        if key not in cache:
            if len(cache) > 8:
                cache.clear()
            cache[key] = raw(rows)
        return cache[key]
    setattr(mod, name, f)


def _key(rows):
    b = rows[-1]
    return (len(rows), int(rows[0]["ts"]), int(b["ts"]), float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))


def replay(S, start, end):
    from src.brain import s1_engine
    from src.brain.weather import classify, side_allowed
    _memo(s1_engine, "m5_structure_events", _key)
    _memo(s1_engine, "last_15m_break", _key)
    s1_engine.reset_s1_state()
    aux_cache, wx_cache = {}, {}
    fires, polls = [], 0
    last_fired_5m = None
    t = start - start % 300 + 300
    t0 = time.time()
    next_note = t0 + 30
    while t <= end:
        c15 = S["15m"].closed_upto(t, LOOKBACK["15m"])
        c5 = S["5m"].closed_upto(t, LOOKBACK["5m"])
        c1 = S["1m"].closed_upto(t, LOOKBACK["1m"])
        watching = False
        if len(c15) >= 80 and len(c5) >= 80 and c1:
            fill = c5[-1]
            k15 = c15[-1]["ts"]
            if k15 not in aux_cache:
                aux_cache.clear()
                aux_cache[k15] = H._aux(c15)
            c4 = S["4h"].closed_upto(t, LOOKBACK["4h"])
            c1h = S["1h"].closed_upto(t, LOOKBACK["1h"])
            kw = (c4[-1]["ts"] if c4 else None, c1h[-1]["ts"] if c1h else None)
            if kw not in wx_cache:
                wx_cache.clear()
                try:
                    wx_cache[kw] = classify(c4, c1h or []) if c4 else None
                except Exception:
                    wx_cache[kw] = None
            prev = None
            for _ in range(MAX_POLLS):
                polls += 1
                try:
                    out = s1_engine.evaluate_s1(c15, fill, candles_5m=c5, candles_1m=c1, aux=aux_cache[k15])
                except Exception as e:
                    out = {"action": "WAIT", "why_state": [f"s1 error: {e}"]}
                if out.get("action") == "FIRE" and fill["ts"] != last_fired_5m:
                    f = take_fire(out, t, fill, c1, wx_cache[kw], side_allowed)
                    if f:
                        last_fired_5m = fill["ts"]
                        fires.append(f)
                stt = s1_engine._STATE
                sig = (out.get("action"), stt.get("phase"),
                       json.dumps(stt.get("c_watch"), default=str, sort_keys=True))
                if sig == prev:
                    break
                prev = sig
            watching = s1_engine._STATE.get("phase") == "C_WATCH"
        if time.time() > next_note:
            next_note = time.time() + 30
            done = (t - start) / max(1, end - start)
            el = time.time() - t0
            print(f"  [S1] {iso(t)}  FIREs {len(fires)}  eta {el / max(done, 1e-6) * (1 - done) / 60:.0f} min", flush=True)
        t += 60 if watching else (300 - t % 300 if t % 300 else 300)
    print(f"  [S1] done: {len(fires)} FIREs, {polls} engine polls, {(time.time() - t0) / 60:.1f} min")
    return fires


def take_fire(out, t, fill, c1, wx, side_allowed):
    entry, stop, target, side = out.get("entry"), out.get("stop"), out.get("target"), out.get("direction")
    if not entry or not stop or not target or side not in ("LONG", "SHORT"):
        return None  # FIRE BUT NO LEVELS
    flag = (wx or {}).get("flag")
    if flag and not side_allowed(flag, side):
        return None  # WEATHER_BLOCK
    th = out.get("thesis_ts")
    try:
        th = int(th) if th is not None else None
    except (TypeError, ValueError):
        th = None
    return {
        "book": "S1", "t": t, "side": side, "gate": None, "kind": "S1",
        "timing": out.get("timing"), "m5_path": None, "slot": out.get("slot"), "event": out.get("event"),
        "thesis_ts": th, "delay_min": (t - (th + 900)) / 60 if th is not None else None,
        "engine_entry": float(entry), "stop": float(stop), "target": float(target),
        "atr": float(out.get("atr_15m") or 0.0), "fill": float(c1[-1]["close"]), "bar_5m": fill["ts"],
    }


# ------------------------------------------------------------------ report

def report(F, fee, a, L):
    L.append(f"CURRENT S1 engine forensic (Hunt forensic methodology)   taker {fee}%/side both legs, "
             f"horizon {a.horizon_hours:g}h, SL/TP distances from S1's own levels re-anchored on the market fill")
    if F:
        L.append(f"FIREs from {iso(F[0]['t'])} to {iso(F[-1]['t'])}")
    L.append("delay = minutes from the close of the 15m thesis candle to the FIRE (negative = before it closed);"
             "  maxCL = longest run of losing trades")
    half = F[len(F) // 2]["t"] if F else None
    for scope, pick in (("ALL live-eligible FIREs", lambda f: True), ("LIVE-like (1 position, cooldown)", lambda f: f["live_like"])):
        L.append(f"\n== {scope} ==")
        L.append(HDR)
        L.append("-" * len(HDR))
        sel = [f for f in F if pick(f)]
        L.append(row("S1 engine", sel))
        for p in ("S1", "S2"):
            L.append(row(f"   timing {p}", [f for f in sel if f.get("timing") == p]))
        if half:
            L.append(row(f"   1st half (< {iso(half)[:10]})", [f for f in sel if f["t"] < half]))
            L.append(row("   2nd half", [f for f in sel if f["t"] >= half]))
    s = summarize(F)
    if s and s["tight"]:
        L.append(f"note: {s['tight']} FIREs had a stop < 0.25 ATR from the entry (netR inflated there; % is reliable)")

    L.append("\nS1: FIREs by timing path / 5m slot / 15m event")
    by = {}
    for f in F:
        by.setdefault((f.get("timing"), f.get("slot"), f.get("event")), []).append(f)
    for k in sorted(by, key=str):
        v = [f["pnl_pct"] for f in by[k] if f["pnl_pct"] is not None]
        L.append(f"  path={str(k[0]):<4} slot={str(k[1]):<4} event={str(k[2]):<6} n={len(v):>4}  net {sum(v):+8.2f}%  "
                 f"exp {st.mean(v) if v else 0:+.3f}%  win {100 * sum(x > 0 for x in v) / len(v) if v else 0:5.1f}%")

    L.append("\nWhole-day bootstrap (4000x) of S1 expectancy")
    L.append(f"{'set':<34}{'n':>6}{'days':>6}{'mean %':>9}{'95% range':>22}{'p(mean>=0)':>12}")
    for name, fs in (("S1 ALL", F), ("S1 LIVE-like", [f for f in F if f["live_like"]])):
        pairs = [(f["t"], -f["pnl_pct"]) for f in fs if f["pnl_pct"] is not None]
        out = H._day_block_boot(pairs)
        if not out:
            L.append(f"{name:<34}{len(pairs):>6}  too few days")
            continue
        m, lo, hi, p = out
        L.append(f"{name:<34}{len(pairs):>6}{len({t // 86400 for t, _ in pairs}):>6}{-m:>+9.3f}"
                 f"{f'{-hi:+.3f} .. {-lo:+.3f}':>22}{p:>12.4f}")


def _read_hunt_csv(p):
    out = []
    with open(p, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("pnl_pct") in (None, ""):
                continue
            f = {"t": int(r["t"]), "kind": r["kind"], "book": r["book"], "outcome": r["outcome"],
                 "pnl_pct": float(r["pnl_pct"]), "R": float(r["R"]) if r.get("R") else None,
                 "delay_min": float(r["delay_min"]) if r.get("delay_min") else None,
                 "atr": float(r["atr"] or 0), "sl_dist": float(r["sl_dist"] or 0),
                 "live_like": r["live_like"] == "True", "timing": r.get("timing")}
            out.append(f)
    return out


def compare(F, L):
    rows = []
    for name, fn in (("Hunt fac4d0e fresh", "hunt_rearm_forensic.csv"), ("Hunt 3d98c41 fresh", "hunt_rearm_forensic_3d98c41.csv")):
        p = Path(fn)
        if not p.is_file():
            L.append(f"(no {fn} in this folder -- {name} left out of the comparison)")
            continue
        h = _read_hunt_csv(p)
        rows.append((name, [f for f in h if f["book"] == "A" and f["kind"] == "fresh"],
                     [f for f in h if f["book"] == "B" and f["live_like"]]))
    if not rows:
        return
    rows.append(("S1 current", F, [f for f in F if f["live_like"]]))
    hdr = (f"{'':<22}{'FIREs':>6}{'TP':>5}{'SL':>5}{'T/O':>5}{'win%':>6}{'net%':>8}{'exp%':>7}{'netR':>8}"
           f"{'PF':>6}{'maxDD%':>7}{'maxCL':>6}{'dly med':>8}{'first FIRE':>18}{'last FIRE':>18}")
    for scope, idx in (("ALL live-eligible FIREs", 1), ("LIVE-like (1 position, cooldown)", 2)):
        L.append(f"\n== HUNT FRESH vs S1 -- {scope} ==")
        L.append(hdr)
        L.append("-" * len(hdr))
        for r in rows:
            fs = sorted(r[idx], key=lambda f: f["t"])
            s = summarize(fs)
            if not s:
                L.append(f"{r[0]:<22} no trades")
                continue
            L.append(f"{r[0]:<22}{s['n']:>6}{s['tp']:>5}{s['sl']:>5}{s['to']:>5}{s['win']:>6.1f}{s['net']:>8.2f}"
                     f"{s['exp']:>7.3f}{s['netR']:>8.1f}{s['pf']:>6.2f}{s['dd']:>7.2f}{s['cl']:>6}"
                     f"{_n(s['d_med'], '.0f'):>8}{iso(fs[0]['t']):>18}{iso(fs[-1]['t']):>18}")
    L.append("Hunt fresh LIVE-like = book B of the Hunt run (only fresh FIREs, one position at a time).")
    L.append("Check the first/last FIRE columns: the comparison is only like-for-like if the periods match.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(BACKEND / "market_data_clean.db"))
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--horizon-hours", type=float, default=72.0)
    ap.add_argument("--taker-fee", type=float, default=None, help="%% per side; default: your MEXC history, else 0.08")
    ap.add_argument("--mexc-history", default=str(BACKEND / "data" / "mexc_history" / "mexc_history.db"))
    ap.add_argument("--out", default="s1_forensic")
    a = ap.parse_args()

    if not Path(a.db).is_file():
        sys.exit(f"Database not found: {a.db}")
    print(f"Loading candles from {a.db} (read-only) ...")
    S = {tf: Series(load(a.db, a.symbol, tf), tf) for tf in TF}
    for tf, s in S.items():
        print(f"  {tf:>3}: {len(s.rows):>7}  {iso(s.ts[0]) if s.ts else '-'} -> {iso(s.ts[-1]) if s.ts else '-'}")
    if not S["1m"].ts:
        sys.exit("no 1m candles")
    hz = int(a.horizon_hours * 3600)
    start = to_ts(a.start) if a.start else max(S["1m"].ts[0], S["5m"].ts[0]) + 3 * 86400
    end = to_ts(a.end) if a.end else S["1m"].ts[-1] - hz
    _, tk, src_ = fee_rates_from_history(a.mexc_history)
    fee = a.taker_fee if a.taker_fee is not None else (tk if tk is not None else 0.08)
    print(f"Fees: taker {fee}%/side ({src_ if a.taker_fee is None else 'from --taker-fee'})")
    print(f"Replaying the current evaluate_s1 {iso(start)} -> {iso(end)} ...")
    F = replay(S, start, end)
    simulate(F, S["1m"], hz, fee)

    L = []
    report(F, fee, a, L)
    compare(F, L)
    rep = "\n".join(L)
    print("\n" + rep)
    out = Path(a.out)
    write_csv(out.with_suffix(".csv"), F)
    out.with_suffix(".txt").write_text(rep, encoding="utf-8")
    print(f"\nWritten {out.with_suffix('.csv')} (every S1 FIRE, same columns as the Hunt CSV) and {out.with_suffix('.txt')}")


if __name__ == "__main__":
    main()
