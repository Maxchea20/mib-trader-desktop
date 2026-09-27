#!/usr/bin/env python3
"""TEMPORARY, READ-ONLY forensic: did Hunt C-FI's re-arm path cause late/bad entries?

Replays the historical Hunt C-FI brain exactly as it was at commit fac4d0e
(verbatim copies in scripts/forensics/hunt_fac4d0e/) on the local candle DB,
twice, with the same candles and the same execution rules:

  A  ORIGINAL HUNT  -- fac4d0e code, untouched (fresh gates + re-arm).
  B  HUNT NO-REARM  -- the same file with ONE statement changed:
                         lingering = active_setup(..., pivot=5, require_fresh=False) or active_setup(..., pivot=2, ...)
                       becomes
                         lingering = None
                       so _evaluate_hunt_c_fi_core returns the C-fast result exactly as
                       it did whenever no lingering 15m setup existed. Nothing else changes:
                       structure detection, direction, thesis, C-fast, internal setup,
                       V3, M5 fill, S1/S2 C timing, SL/TP, fees, sizing, execution.

Nothing in src/ is modified or imported from these copies; production never sees them.

  cd backend
  python scripts/forensics/hunt_rearm_forensic.py
  python scripts/forensics/hunt_rearm_forensic.py --start 2026-08-01 --end 2026-09-20
  python scripts/forensics/hunt_rearm_forensic.py --hunt 3d98c41     # Hunt BEFORE S1/S2 timing existed

--hunt 3d98c41 replays the older Hunt C-FI (scripts/forensics/hunt_3d98c41/, live until
Sep 24 21:16): no S1/S2 timing layer, slot 1/2 FIRE directly from the V2 M5 fill, slot 3
impulse, weather V1. Same A/B split (same single statement), same execution.

HOW IT MATCHES THE fac4d0e LIVE LOOP
  * Inputs as analysis_observations.collect_observations built them: closed 15m (320),
    closed 5m (959 = 960 read incl. the forming bar), 1m closed (400), 4h (300), 1h (400),
    live_5ms = closed 5m of the current 15m parent, aux mom/vol/sr/fvg on the 15m.
  * The engine is polled at every 5m close and at every 1m close while the timing layer
    is in C_WATCH. Live polled every few seconds with unchanged candles, so each moment is
    polled repeatedly until the timing state stops changing (same result as the 5 s poll).
  * FIRE is taken as the loop took it: entry/stop/target (with the nested hunt fallback),
    4h weather must allow the side, not already fired this 5m.
  * Execution = _open_live_from_hunt at fac4d0e: market fill at the fresh price (last
    closed 1m close), SL/TP DISTANCES = |entry-stop| and |target-entry| from Hunt,
    re-anchored on the fill. Bracket on 1m candles, SL first if both touch, horizon 72h.
    Taker fee both sides (read from your MEXC history, else 0.08 %/side).
  * LIVE-like = one position at a time + 3x5m cooldown after an SL (fac4d0e _in_cooldown).
    Lifecycle BRAIN_EXIT/trail is not modelled (same in both books).

FRESH vs RE-ARM: a FIRE is RE-ARM when its gate is rearm_cfast / rearm_internal
(rearm=True), i.e. it rode a lingering (not fresh) 15m thesis; FRESH = gate cfast/internal.
Thesis-to-FIRE delay = FIRE time - close of the 15m candle that printed the thesis event.

OUTPUT: summary table per book (ALL and LIVE-like, split fresh / re-arm), A-vs-B trade
matching, bootstrap on re-arm expectancy, and hunt_rearm_trades.csv (every FIRE, both books).
"""
from __future__ import annotations

import argparse
import copy
import csv
import importlib
import importlib.util
import json
import statistics as st
import subprocess
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

# reuse the existing replay runner's candle loader, fee reader and day-block bootstrap
from s1_entry_study import Series, load, iso, to_ts, fee_rates_from_history, _day_block_boot, TF  # noqa: E402

# fac4d0e = last intact Hunt C-FI (slot 1/2 entries through its built-in S1/S2 C timing).
# 3d98c41 = Hunt C-FI BEFORE the S1/S2 timing layer existed (live Sep 20 -> Sep 24 21:16 UTC+8):
#           slot 1/2 FIRE straight from the V2 M5 fill, slot 3 impulse, weather V1.
_CORE = ("observation_hunt", "observation_hunt_c", "observation_hunt_c_fast", "observation_hunt_v3", "observation_hunt_c_fi")
HUNTS = {
    "fac4d0e": _CORE + ("hunt_entry_timing", "entry_timing_c", "weather"),
    "3d98c41": _CORE + ("weather",),
}
SNAP = HERE / "hunt_fac4d0e"
PKG = "src.hunt_fac4d0e"  # virtual package: relative imports '..contract' resolve to production src (unchanged since 3d98c41)
SNAP_FILES = HUNTS["fac4d0e"]
COMMIT = "fac4d0e"
LOOKBACK = {"15m": 320, "5m": 959, "1m": 400, "1h": 400, "4h": 300}
COOLDOWN_S = 3 * 300  # cooldown_bars_after_failure x 5m
MAX_POLLS = 8

REARM_STMT = (
    "    lingering = active_setup(candles_15m, pivot=5, require_fresh=False) or active_setup(\n"
    "        candles_15m, pivot=2, require_fresh=False\n"
    "    )\n"
)
NO_REARM_STMT = "    lingering = None  # BOOK B: the only change -- re-arm / lingering thesis path disabled\n"


# ------------------------------------------------------------------ load the fac4d0e code

def verify_snapshot(snap):
    """Compare the copies with git's fac4d0e objects when git is available."""
    out = []
    for f in SNAP_FILES:
        try:
            ref = subprocess.run(["git", "show", f"{COMMIT}:backend/src/brain/{f}.py"], cwd=str(BACKEND),
                                 capture_output=True, timeout=30)
        except Exception as e:
            return [f"git not available ({e}) -- snapshot not verified"]
        if ref.returncode != 0:
            return [f"commit {COMMIT} not in this clone (git fetch?) -- snapshot not verified"]
        same = ref.stdout.replace(b"\r\n", b"\n") == (snap / f"{f}.py").read_bytes().replace(b"\r\n", b"\n")
        out.append(f"{f}.py {'== ' + COMMIT if same else 'DIFFERS from ' + COMMIT}")
    return out


def load_books(snap):
    if PKG not in sys.modules:
        import src  # noqa: F401  production package (only its unchanged non-brain modules get used)
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(snap)]
        pkg.__package__ = PKG
        sys.modules[PKG] = pkg
    book_a = importlib.import_module(PKG + ".observation_hunt_c_fi")
    src_text = (snap / "observation_hunt_c_fi.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    if src_text.count(REARM_STMT) != 1:
        sys.exit("re-arm statement not found exactly once in observation_hunt_c_fi.py -- refusing to guess")
    name = PKG + ".observation_hunt_c_fi_norearm"
    spec = importlib.util.spec_from_loader(name, loader=None)
    book_b = importlib.util.module_from_spec(spec)
    book_b.__file__ = str(snap / "observation_hunt_c_fi.py") + " [BOOK B]"
    sys.modules[name] = book_b
    exec(compile(src_text.replace(REARM_STMT, NO_REARM_STMT), book_b.__file__, "exec"), book_b.__dict__)
    timing = importlib.import_module(PKG + ".hunt_entry_timing") if "hunt_entry_timing" in SNAP_FILES else None
    weather = importlib.import_module(PKG + ".weather")
    hunt_c = importlib.import_module(PKG + ".observation_hunt_c")
    return book_a, book_b, timing, weather, hunt_c


# ------------------------------------------------------------------ replay (one book)

def _memo_m5_events(timing):
    """m5_structure_events is a pure function of the 5m list; memoise per last bar (speed only)."""
    raw = timing.m5_structure_events
    cache = {}

    def m5_structure_events(candles_5m):
        if not candles_5m:
            return raw(candles_5m)
        k = (len(candles_5m), int(candles_5m[0]["ts"]), int(candles_5m[-1]["ts"]), float(candles_5m[-1]["close"]))
        if k not in cache:
            if len(cache) > 8:
                cache.clear()
            cache[k] = raw(candles_5m)
        return cache[k]
    timing.m5_structure_events = m5_structure_events
    return raw


def _aux(c15):
    try:
        from src.fair_value_gap.observe import observe as obs_fvg
        from src.momentum.observe import observe as obs_mom
        from src.support_resistance.observe import observe as obs_sr
        from src.volume.observe import observe as obs_vol
        return {"mom": obs_mom(c15, "15m"), "vol": obs_vol(c15, "15m"), "sr": obs_sr(c15, "15m"), "fvg": obs_fvg(c15, "15m")}
    except Exception:
        return {}  # the live code also fell back to {} on any error


def replay(book, label, S, start, end, timing, weather, hunt_c):
    if timing is not None:
        timing.reset_timing_state()
    core_cache, aux_cache, wx_cache = {}, {}, {}
    fires = []
    last_fired_5m = None
    polls = 0
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
            po = hunt_c.parent_open(fill["ts"])
            live = [c for c in c5 if hunt_c.parent_open(c["ts"]) == po]
            c4 = S["4h"].closed_upto(t, LOOKBACK["4h"])
            c1h = S["1h"].closed_upto(t, LOOKBACK["1h"])
            k15 = c15[-1]["ts"]
            if k15 not in aux_cache:
                aux_cache.clear()
                aux_cache[k15] = _aux(c15)
            kw = (c4[-1]["ts"] if c4 else None, c1h[-1]["ts"] if c1h else None)
            if kw not in wx_cache:
                wx_cache.clear()
                try:
                    wx_cache[kw] = weather.classify(c4, c1h or []) if c4 else None
                except Exception:
                    wx_cache[kw] = None
            k5 = (k15, fill["ts"])
            if k5 not in core_cache:  # the core is pure: same candles -> same output
                core_cache.clear()
                core = book._evaluate_hunt_c_fi_core if timing is not None else book.evaluate_hunt_c_fi
                try:
                    core_cache[k5] = core(c15, fill, live, c4, c1h, c5)
                except Exception as e:
                    core_cache[k5] = {"action": "WAIT", "why_state": [f"hunt C-FI error: {e}"], "ok": True}
            prev = None
            for _ in range(MAX_POLLS):  # live polled every few seconds on unchanged candles
                polls += 1
                try:
                    out = copy.deepcopy(core_cache[k5])
                    if timing is not None:
                        out = book._with_timing(out, c15, fill, live, c5, c1, aux_cache[k15])
                except Exception as e:
                    out = {"action": "WAIT", "why_state": [f"hunt C-FI error: {e}"]}
                if out.get("action") == "FIRE" and fill["ts"] != last_fired_5m:
                    f = take_fire(out, t, fill, c1, wx_cache[kw], weather)
                    if f:
                        last_fired_5m = fill["ts"]
                        f["book"] = label
                        fires.append(f)
                if timing is None:  # no timing layer: output depends on closed candles only
                    break
                phase = timing._STATE.get("phase")
                sig = (out.get("action"), phase, json.dumps(timing._STATE.get("c_watch"), default=str, sort_keys=True))
                if sig == prev:
                    break
                prev = sig
            watching = timing is not None and timing._STATE.get("phase") == "C_WATCH"
        if time.time() > next_note:
            next_note = time.time() + 30
            done = (t - start) / max(1, end - start)
            el = time.time() - t0
            print(f"  [{label}] {iso(t)}  FIREs {len(fires)}  eta {el / max(done, 1e-6) * (1 - done) / 60:.0f} min", flush=True)
        t += 60 if watching else (300 - t % 300 if t % 300 else 300)
    print(f"  [{label}] done: {len(fires)} FIREs, {polls} engine polls, {(time.time() - t0) / 60:.1f} min")
    return fires


def take_fire(out, t, fill, c1, wx, weather):
    nested = out.get("hunt") if isinstance(out.get("hunt"), dict) else {}
    entry = out.get("entry") or nested.get("level") or nested.get("entry")
    stop = out.get("stop") or nested.get("stop")
    target = out.get("target") or nested.get("target")
    side = out.get("direction")
    if not entry or not stop or not target or side not in ("LONG", "SHORT"):
        return None  # FIRE BUT NO LEVELS
    flag = (wx or {}).get("flag")
    if flag and not weather.side_allowed(flag, side):
        return None  # WEATHER_BLOCK
    gate = out.get("gate")
    rearm = bool(out.get("rearm")) or str(gate or "").startswith("rearm")
    th = out.get("thesis_ts")
    try:
        th = int(th) if th is not None else None
    except (TypeError, ValueError):
        th = None
    return {
        "t": t, "side": side, "gate": gate, "kind": "rearm" if rearm else "fresh",
        "timing": out.get("timing"), "m5_path": nested.get("m5_path") or out.get("v3a_path"),
        "slot": out.get("slot"), "event": out.get("event"),
        "thesis_ts": th, "delay_min": (t - (th + 900)) / 60 if th is not None else None,
        "engine_entry": float(entry), "stop": float(stop), "target": float(target),
        "atr": float(out.get("atr_15m") or 0.0), "fill": float(c1[-1]["close"]), "bar_5m": fill["ts"],
    }


# ------------------------------------------------------------------ execution

def run_trade(f, one_min, horizon_s, fee):
    rows, ts = one_min.rows, one_min.ts
    long_ = f["side"] == "LONG"
    px = f["fill"]
    sd = abs(f["engine_entry"] - f["stop"])  # _open_live_from_hunt: distances re-anchored on the fresh price
    td = abs(f["target"] - f["engine_entry"])
    f["sl_dist"], f["tp_dist"] = sd, td
    if sd <= 0 or td <= 0:
        return {"out": "BAD_LEVELS", "pnl": None}
    sl, tp = (px - sd, px + td) if long_ else (px + sd, px - td)
    fees = 2 * fee / 100 * px
    import bisect
    i = bisect.bisect_left(ts, f["t"])
    end, last = f["t"] + horizon_s, None
    while i < len(rows) and rows[i]["ts"] < end:
        c = rows[i]
        if (c["low"] <= sl) if long_ else (c["high"] >= sl):
            return {"out": "SL", "pnl": -sd - fees, "exit_ts": c["ts"] + 60}
        if (c["high"] >= tp) if long_ else (c["low"] <= tp):
            return {"out": "TP", "pnl": td - fees, "exit_ts": c["ts"] + 60}
        last = c["close"]
        i += 1
    if last is None:
        return {"out": "NO_DATA", "pnl": None}
    return {"out": "TIMEOUT", "pnl": ((last - px) if long_ else (px - last)) - fees, "exit_ts": rows[i - 1]["ts"] + 60}


def simulate(fires, one_min, horizon_s, fee):
    for f in fires:
        r = run_trade(f, one_min, horizon_s, fee)
        f.update({"outcome": r["out"], "exit_ts": r.get("exit_ts")})
        f["pnl_pct"] = 100 * r["pnl"] / f["fill"] if r["pnl"] is not None else None
        f["R"] = r["pnl"] / f["sl_dist"] if r["pnl"] is not None and f.get("sl_dist") else None
    live_like(fires)


def live_like(fires):
    busy = cool = 0
    for f in fires:
        f["live_like"] = False
        if f["pnl_pct"] is None or f["t"] < busy or f["t"] < cool:
            continue
        f["live_like"] = True
        busy = f["exit_ts"]
        cool = busy + COOLDOWN_S if f["outcome"] == "SL" else 0


# ------------------------------------------------------------------ report

def summarize(fs):
    fs = [f for f in fs if f["pnl_pct"] is not None]
    if not fs:
        return None
    pct = [f["pnl_pct"] for f in fs]
    R = [f["R"] for f in fs if f["R"] is not None]
    gp, gl = sum(v for v in pct if v > 0), -sum(v for v in pct if v < 0)
    eq = pk = dd = 0.0
    streak = mx = 0
    for v in pct:
        eq += v
        pk = max(pk, eq)
        dd = max(dd, pk - eq)
        streak = streak + 1 if v <= 0 else 0
        mx = max(mx, streak)
    d = [f["delay_min"] for f in fs if f["delay_min"] is not None]
    return {
        "n": len(fs), "fresh": sum(f["kind"] == "fresh" for f in fs), "rearm": sum(f["kind"] == "rearm" for f in fs),
        "tp": sum(f["outcome"] == "TP" for f in fs), "sl": sum(f["outcome"] == "SL" for f in fs),
        "to": sum(f["outcome"] == "TIMEOUT" for f in fs),
        "wins": sum(v > 0 for v in pct), "losses": sum(v <= 0 for v in pct),
        "win": 100 * sum(v > 0 for v in pct) / len(pct), "net": sum(pct), "exp": st.mean(pct),
        "netR": sum(R), "pf": gp / gl if gl else float("inf"), "dd": dd, "cl": mx,
        "d_avg": st.mean(d) if d else None, "d_med": st.median(d) if d else None, "d_max": max(d) if d else None,
        "tight": sum(1 for f in fs if f["atr"] and f["sl_dist"] < 0.25 * f["atr"]),
    }


HDR = (f"{'':<22}{'FIREs':>6}{'fresh':>6}{'rearm':>6}{'TP':>5}{'SL':>5}{'T/O':>5}{'W':>5}{'L':>5}{'win%':>6}"
       f"{'net%':>8}{'exp%':>7}{'netR':>8}{'PF':>6}{'maxDD%':>7}{'maxCL':>6}{'dly avg':>8}{'med':>6}{'max':>7}")


def _n(v, fmt):
    return "-" if v is None else format(v, fmt)


def row(name, s):
    if not s:
        return f"{name:<22} no trades"
    return (f"{name:<22}{s['n']:>6}{s['fresh']:>6}{s['rearm']:>6}{s['tp']:>5}{s['sl']:>5}{s['to']:>5}{s['wins']:>5}"
            f"{s['losses']:>5}{s['win']:>6.1f}{s['net']:>8.2f}{s['exp']:>7.3f}{s['netR']:>8.1f}{s['pf']:>6.2f}"
            f"{s['dd']:>7.2f}{s['cl']:>6}{_n(s['d_avg'], '.0f'):>8}{_n(s['d_med'], '.0f'):>6}{_n(s['d_max'], '.0f'):>7}")


def report(A, B, fee, a, L):
    L.append(f"Hunt C-FI {COMMIT} re-arm forensic   taker {fee}%/side both legs, horizon {a.horizon_hours:g}h, "
             "SL/TP distances from Hunt's own levels re-anchored on the market fill")
    L.append("delay = minutes from the close of the 15m thesis candle to the FIRE;  maxCL = longest run of losing trades;")
    L.append("netR uses each trade's own stop distance (can be large when a stop is tight -- see 'tight' note)")
    half = None
    allf = sorted(A + B, key=lambda f: f["t"])
    if allf:
        half = allf[len(allf) // 2]["t"]
    for scope, pick in (("ALL live-eligible FIREs", lambda f: True), ("LIVE-like (1 position, cooldown)", lambda f: f["live_like"])):
        L.append(f"\n== {scope} ==")
        L.append(HDR)
        L.append("-" * len(HDR))
        for label, fs in (("A original", A), ("B no-rearm", B)):
            sel = [f for f in fs if pick(f)]
            L.append(row(f"{label}", summarize(sel)))
            L.append(row("   fresh FIREs", summarize([f for f in sel if f["kind"] == "fresh"])))
            L.append(row("   re-arm FIREs", summarize([f for f in sel if f["kind"] == "rearm"])))
        if half:
            for label, fs in (("A", A), ("B", B)):
                for hl, cond in (("1st half", lambda f: f["t"] < half), ("2nd half", lambda f: f["t"] >= half)):
                    L.append(row(f"   {label} {hl}", summarize([f for f in fs if pick(f) and cond(f)])))
    for label, fs in (("A", A), ("B", B)):
        s = summarize(fs)
        if s and s["tight"]:
            L.append(f"note {label}: {s['tight']} FIREs had a stop < 0.25 ATR from the entry (netR inflated there; % is reliable)")

    # A vs B trade matching
    L.append("\n== A vs B, same FIRE = same 5m bar + side ==")
    kb = {(f["bar_5m"], f["side"]): f for f in B}
    ka = {(f["bar_5m"], f["side"]): f for f in A}
    both = [f for k, f in ka.items() if k in kb]
    a_only = [f for k, f in ka.items() if k not in kb]
    b_only = [f for k, f in kb.items() if k not in ka]

    def g(fs):
        v = [f["pnl_pct"] for f in fs if f["pnl_pct"] is not None]
        return f"n={len(v):>4}  net {sum(v):+8.2f}%  exp {st.mean(v) if v else 0:+.3f}%  win {100 * sum(x > 0 for x in v) / len(v) if v else 0:5.1f}%"
    L.append(f"  in both books          {g(both)}")
    L.append(f"  only in A              {g(a_only)}")
    L.append(f"     of which re-arm     {g([f for f in a_only if f['kind'] == 'rearm'])}")
    L.append(f"     of which fresh      {g([f for f in a_only if f['kind'] == 'fresh'])}  (timing state differed because re-arm kept it alive)")
    L.append(f"  only in B              {g(b_only)}")
    moved = sum(1 for f in both if f["t"] != kb[(f["bar_5m"], f["side"])]["t"])
    L.append(f"  shared FIREs that fired at a different minute in A and B: {moved}")

    L.append("\n== Is re-arm expectancy different from zero? (whole UTC days resampled 4000x) ==")
    L.append(f"{'set':<34}{'n':>6}{'days':>6}{'mean %':>9}{'95% range':>22}{'p(mean>=0)':>12}")
    for name, fs in (("A re-arm FIREs, ALL", [f for f in A if f["kind"] == "rearm"]),
                     ("A fresh FIREs, ALL", [f for f in A if f["kind"] == "fresh"]),
                     ("A re-arm FIREs, LIVE-like", [f for f in A if f["kind"] == "rearm" and f["live_like"]]),
                     ("A only (lost when re-arm is off)", a_only),
                     ("B only (gained when re-arm is off)", b_only)):
        pairs = [(f["t"], -f["pnl_pct"]) for f in fs if f["pnl_pct"] is not None]
        out = _day_block_boot(pairs)
        if not out:
            L.append(f"{name:<34}{len(pairs):>6}  too few days")
            continue
        m, lo, hi, p = out  # computed on -pnl: p = P(mean(-pnl) <= 0) = P(mean pnl >= 0)
        L.append(f"{name:<34}{len(pairs):>6}{len({t // 86400 for t, _ in pairs}):>6}{-m:>+9.3f}"
                 f"{f'{-hi:+.3f} .. {-lo:+.3f}':>22}{p:>12.4f}")
    L.append("Read: re-arm HURTS if its mean is < 0 with the 95% range below 0 (p(mean>=0) < 0.05), in ALL and LIVE-like,")
    L.append("and B's LIVE-like net% beats A's. Compare both halves before trusting it.")

    for label, fs in (("A", A), ("B", B)):
        by = {}
        for f in fs:
            by.setdefault((f["kind"], f.get("gate"), f.get("timing") or f.get("m5_path")), []).append(f)
        L.append(f"\n{label}: FIREs by gate / timing path")
        for k in sorted(by, key=str):
            L.append(f"  {str(k[0]):<6} gate={str(k[1]):<15} path={str(k[2]):<18} {g(by[k])}")


def write_csv(path, fires):
    cols = ["book", "time_utc", "t", "side", "kind", "gate", "timing", "m5_path", "slot", "event", "thesis_utc",
            "delay_min", "engine_entry", "stop", "target", "fill", "sl_dist", "tp_dist", "atr", "outcome",
            "pnl_pct", "R", "exit_utc", "live_like"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for f in fires:
            r = dict(f)
            r["time_utc"] = iso(f["t"])
            r["thesis_utc"] = iso(f["thesis_ts"]) if f.get("thesis_ts") else ""
            r["exit_utc"] = iso(f["exit_ts"]) if f.get("exit_ts") else ""
            for k in ("delay_min", "pnl_pct", "R"):
                if r.get(k) is not None:
                    r[k] = round(r[k], 4)
            w.writerow([r.get(c, "") if r.get(c) is not None else "" for c in cols])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(BACKEND / "market_data_clean.db"))
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--horizon-hours", type=float, default=72.0)
    ap.add_argument("--taker-fee", type=float, default=None, help="%% per side; default: your MEXC history, else 0.08")
    ap.add_argument("--mexc-history", default=str(BACKEND / "data" / "mexc_history" / "mexc_history.db"))
    ap.add_argument("--hunt", choices=sorted(HUNTS), default="fac4d0e",
                    help="fac4d0e = with Hunt's S1/S2 C timing (default); 3d98c41 = Hunt before S1/S2 timing existed")
    ap.add_argument("--out", default=None, help="output base name (default hunt_rearm_forensic[_3d98c41])")
    ap.add_argument("--snapshot-dir", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args()

    global SNAP, PKG, SNAP_FILES, COMMIT
    COMMIT, SNAP_FILES = a.hunt, HUNTS[a.hunt]
    SNAP, PKG = HERE / f"hunt_{a.hunt}", f"src.hunt_{a.hunt}"
    if a.out is None:
        a.out = "hunt_rearm_forensic" + ("" if a.hunt == "fac4d0e" else f"_{a.hunt}")
    snap = Path(a.snapshot_dir) if a.snapshot_dir else SNAP
    if not Path(a.db).is_file():
        sys.exit(f"Database not found: {a.db}")
    print("Snapshot check:")
    for line in verify_snapshot(snap):
        print("  " + line)
    book_a, book_b, timing, weather, hunt_c = load_books(snap)
    print(f"Book B = {COMMIT} observation_hunt_c_fi.py with this single replacement:")
    print("  - " + REARM_STMT.strip().replace("\n", " ").replace("    ", ""))
    print("  + " + NO_REARM_STMT.strip())
    if timing is not None:
        _memo_m5_events(timing)

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
    print(f"Replaying {iso(start)} -> {iso(end)} (same candles for both books) ...")
    A = replay(book_a, "A", S, start, end, timing, weather, hunt_c)
    B = replay(book_b, "B", S, start, end, timing, weather, hunt_c)
    simulate(A, S["1m"], hz, fee)
    simulate(B, S["1m"], hz, fee)

    L = []
    report(A, B, fee, a, L)
    rep = "\n".join(L)
    print("\n" + rep)
    out = Path(a.out)
    write_csv(out.with_suffix(".csv"), A + B)
    out.with_suffix(".txt").write_text(rep, encoding="utf-8")
    print(f"\nWritten {out.with_suffix('.csv')} (every FIRE, both books) and {out.with_suffix('.txt')}")


if __name__ == "__main__":
    main()
