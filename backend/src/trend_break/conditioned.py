"""RESEARCH ONLY -- Conditioned TP overlay on the fixed-SL / fixed-TP backtest trades.

Not imported by the engine, the autotrader or any live path.  Entries, the fixed
1.5 ATR stop and the fixed 3.0 ATR target are untouched: every trade keeps its baseline
outcome unless the conditioned rule fires FIRST.

Rule (pre-registered, not tuned):
  1. The trade must first reach +1.5R (candle-close availability, as in the forensics).
  2. An opposite 1M CHoCH after that is only a WARNING (no exit).  The favourable
     extreme reached up to that moment is remembered.
  3. If a later closed 1M candle makes a NEW favourable extreme beyond it, the warning
     is CANCELLED and the trade is held.  A later opposite 1M CHoCH can warn again.
  4. While warned (no new extreme), the first 5M-or-higher structural failure that becomes
     AVAILABLE STRICTLY AFTER the warning triggers the conditioned exit.  Trigger set:
        M5_opp_BOS, M5_opp_CHoCH, M15_opp_BOS, M15_opp_CHoCH,
        LOST_5m, LOST_15m (a 5M/15M close back through the original breakout level),
        LINE_LOST_setup (a setup-timeframe close back through the projected trendline).
     (1M events, ENGINE_INVALID and 1H events are NOT triggers.)
  5. Exit price = the OPEN of the first 1M candle after the candle whose close made the
     condition true.  It only counts if that candle opens no later than the baseline
     exit candle (otherwise the baseline SL/TP happened first).
  Ties at the same candle close: the trigger is evaluated BEFORE the new-extreme check
  (conservative for the rule; counted and reported).
"""
from __future__ import annotations

from typing import Dict, List

from .diagnostics import diagnose_trade
from .forensics import BACK, SEC, _fmt_t, _window, analyze, sequence
from .gauges import structure_events

TRIGGERS = ("M5_opp_BOS", "M5_opp_CHoCH", "M15_opp_BOS", "M15_opp_CHoCH", "LOST_5m", "LOST_15m", "LINE_LOST_setup")
EPS = 1e-9


def state_machine(path, t_ref, warn_avails, trig) -> Dict:
    """path: [(avail, favR)] of 1M candles closed before the baseline exit candle opens.
    warn_avails: set of avail times of opposite 1M CHoCH events.  trig: {avail: [names]}."""
    ext, state, warn, wext = 0.0, "PRE", None, None
    log: List[tuple] = []
    for j, (avail, fav) in enumerate(path):
        if state == "WARNED" and avail > warn and trig.get(avail):
            return {"exit_j": j, "warn": warn, "trigger": list(trig[avail]), "trigger_avail": avail,
                    "tie_with_new_extreme": fav > wext + EPS, "log": log}
        ext = max(ext, fav)
        if state == "PRE":
            if avail >= t_ref:
                state = "ARMED"
        elif state == "WARNED" and fav > wext + EPS:
            state = "ARMED"
            log.append(("cancel", avail))
        if state == "ARMED" and avail > t_ref and avail in warn_avails:
            state, warn, wext = "WARNED", avail, ext
            log.append(("warn", avail))
    return {"exit_j": None, "warn": warn, "log": log}


def _events(trade, t_ref, t_end, rows, opens):
    side = trade["side"]
    t0 = trade["fire_ts"]
    warn = set()
    for e in structure_events(_window(rows, opens, "1m", t0 - BACK["1m"] * 60, t_end)):
        avail = e["ts"] + 60
        if e["direction"] != side and e["type"] == "CHoCH" and avail > t_ref:
            warn.add(avail)
    trig: Dict[int, List[str]] = {}
    for tf, tag in (("5m", "M5"), ("15m", "M15")):
        for e in structure_events(_window(rows, opens, tf, t0 - BACK[tf] * SEC[tf], t_end)):
            avail = e["ts"] + SEC[tf]
            if e["direction"] != side and t_ref < avail <= t_end:
                trig.setdefault(avail, []).append(f"{tag}_opp_{e['type']}")
    lvl = trade["break_level"]
    beyond = (lambda c, x: c < x) if side == "LONG" else (lambda c, x: c > x)
    for tf in ("5m", "15m"):
        for c in _window(rows, opens, tf, t_ref - SEC[tf] + 1, t_end):
            if beyond(float(c["close"]), lvl):
                trig.setdefault(int(c["ts"]) + SEC[tf], []).append(f"LOST_{tf}")
    bl, stf = trade.get("break_line"), trade["setup_tf"]
    if bl:
        sign = -1.0 if bl["line_kind"] == "upper" else 1.0
        for c in _window(rows, opens, stf, t_ref - SEC[stf] + 1, t_end):
            v = bl["pivot_price"] + sign * bl["slope"] * (int(c["ts"]) - bl["pivot_ts"]) / SEC[stf]
            if beyond(float(c["close"]), v):
                trig.setdefault(int(c["ts"]) + SEC[stf], []).append("LINE_LOST_setup")
    return warn, {a: sorted(set(n)) for a, n in trig.items() if a <= t_end}


def overlay(trade: dict, diag: dict, rows, opens, m: float = 1.5) -> Dict:
    """Apply the rule to one trade.  Returns the conditioned outcome record."""
    base_r = diag["final_r"]
    rec = {"base_outcome": diag["outcome"], "base_r": base_r, "outcome": diag["outcome"], "r": base_r,
           "reached": bool(diag["milestones"].get(m)), "mfe": diag["mfe"], "log": [], "n_warn": 0, "n_cancel": 0}
    ms = diag["milestones"].get(m)
    if not ms or diag["exit_ts"] is None:
        return rec
    entry, risk, s = trade["entry"], trade["_risk"], (1.0 if trade["side"] == "LONG" else -1.0)
    t_ref, t_end = ms["reach_ts"] + 60, diag["exit_ts"]
    rec["t_ref"] = t_ref
    warn, trig = _events(trade, t_ref, t_end, rows, opens)
    m1 = rows["1m"]
    idxs, path = [], []
    for k in range(trade["i"] + 1, len(m1)):
        c = m1[k]
        if int(c["ts"]) + 60 > t_end:
            break
        h, l = float(c["high"]), float(c["low"])
        path.append((int(c["ts"]) + 60, ((h - entry) if s > 0 else (entry - l)) / risk))
        idxs.append(k)
    sm = state_machine(path, t_ref, warn, {a: n for a, n in trig.items() if any(x in TRIGGERS for x in n)})
    rec["log"] = sm["log"]
    rec["n_warn"] = sum(1 for k, _ in sm["log"] if k == "warn")
    rec["n_cancel"] = sum(1 for k, _ in sm["log"] if k == "cancel")
    if sm["exit_j"] is None:
        return rec
    nxt = m1[idxs[sm["exit_j"]] + 1]
    px = float(nxt["open"])
    rec.update({"outcome": "CTP", "r": ((px - entry) if s > 0 else (entry - px)) / risk, "exit_ts": int(nxt["ts"]),
                "exit_price": px, "warn": sm["warn"], "trigger": sm["trigger"], "trigger_avail": sm["trigger_avail"],
                "tie": sm["tie_with_new_extreme"]})
    # what happened afterwards (cost analysis only; never used to decide the exit)
    hi, lo = rec["r"], rec["r"]
    for k in range(idxs[sm["exit_j"]] + 1, len(m1)):
        c = m1[k]
        if int(c["ts"]) > t_end:
            break
        h, l = float(c["high"]), float(c["low"])
        hi = max(hi, ((h - entry) if s > 0 else (entry - l)) / risk)
        lo = min(lo, ((l - entry) if s > 0 else (entry - h)) / risk)
    rec["after_max_r"], rec["after_min_r"] = hi, lo
    return rec


def _stats(rs):
    n = len(rs)
    gp = sum(r for r in rs if r > 0)
    gl = -sum(r for r in rs if r < 0)
    return {"n": n, "net": sum(rs), "avg": (sum(rs) / n if n else 0.0), "pf": (gp / gl if gl else float("inf")),
            "wins": sum(1 for r in rs if r > 0)}


def report(trades: List[dict], rows, opens, m: float = 1.5) -> str:
    out: List[str] = []
    p = out.append
    trades = [t for t in trades if t.get("exit")]
    diags = [diagnose_trade(t, rows["1m"]) for t in trades]
    recs = [overlay(t, d, rows, opens, m) for t, d in zip(trades, diags)]
    N = len(trades)
    base = [r["base_r"] for r in recs]
    cond = [r["r"] for r in recs]
    bs, cs = _stats(base), _stats(cond)
    ctp = [(t, d, r) for t, d, r in zip(trades, diags, recs) if r["outcome"] == "CTP"]
    ntp = sum(1 for r in recs if r["outcome"] == "TP")
    nsl = sum(1 for r in recs if r["outcome"] == "SL")
    btp = sum(1 for r in recs if r["base_outcome"] == "TP")
    bsl = sum(1 for r in recs if r["base_outcome"] == "SL")
    mism = sum(1 for t, d in zip(trades, diags) if d["outcome"] != t["exit"])

    def f(x, nd=2):
        return "inf" if x == float("inf") else f"{x:.{nd}f}"

    p("=" * 78)
    p("CONDITIONED TP -- research overlay on the SAME trades (engine, entries, fixed SL/TP unchanged)")
    p(f"replay vs backtest exit mismatches: {mism} (must be 0)")
    p("Trigger set: M5_opp_BOS/CHoCH, M15_opp_BOS/CHoCH, LOST_5m/15m, LINE_LOST_setup, evaluated strictly AFTER the")
    p("1M CHoCH warning and before any new favourable extreme.  Exit at the OPEN of the next 1M candle.")
    p("=" * 78)
    p("\nSECTION 1 -- BASELINE (fixed 1.5 ATR SL / 3.0 ATR TP)")
    p(f"  Trades {N}   TP {btp}   SL {bsl}   Win rate {100 * bs['wins'] / N:.1f}%   Net R {bs['net']:+.2f}   "
      f"Avg R/trade {bs['avg']:+.3f}   Profit factor {f(bs['pf'])}")
    p("\nSECTION 2 -- CONDITIONED TP")
    p(f"  Trades {N}   Normal TP {ntp}   Conditioned TP {len(ctp)}   SL {nsl}   Win rate {100 * cs['wins'] / N:.1f}%   "
      f"Net R {cs['net']:+.2f}   Avg R/trade {cs['avg']:+.3f}   Profit factor {f(cs['pf'])}")
    gain = sum(r["r"] - r["base_r"] for _, _, r in ctp)
    gain_sl = sum(r["r"] - r["base_r"] for _, _, r in ctp if r["base_outcome"] == "SL")
    gain_tp = sum(r["r"] - r["base_r"] for _, _, r in ctp if r["base_outcome"] == "TP")
    p("\nSECTION 3 -- DIRECT COMPARISON")
    p(f"  {'Metric':30s} {'Baseline':>10s} {'Conditioned':>12s} {'Difference':>11s}")
    for name, a, b in [("TP count", btp, ntp), ("Conditioned TP count", 0, len(ctp)), ("SL count", bsl, nsl),
                       ("Win rate % (R > 0)", 100 * bs['wins'] / N, 100 * cs['wins'] / N),
                       ("Net R", bs['net'], cs['net']), ("Avg R/trade", bs['avg'], cs['avg']),
                       ("Profit factor", bs['pf'], cs['pf'])]:
        d = "-" if (a == float('inf') or b == float('inf')) else f"{b - a:+.3f}"
        p(f"  {name:30s} {f(a, 3):>10s} {f(b, 3):>12s} {d:>11s}")
    p(f"  Total R gained/lost from Conditioned TP exits: {gain:+.2f}R  "
      f"(rescued baseline SLs {gain_sl:+.2f}R; cost on baseline TPs {gain_tp:+.2f}R)")

    p("\nSECTION 4 -- every trade closed by the Conditioned TP")
    p(f"  {'fire':11s} {'S':1s} {'entry':>9s} {'+1.5R':>11s} {'warning':>11s} {'5M+ confirm':>11s} {'exit':>11s} {'exit px':>9s} "
      f"{'exitR':>6s} {'MFE':>5s} {'orig':>4s} {'warn->exit':>10s} {'1.5R->exit':>10s}  trigger")
    for t, d, r in ctp:
        p(f"  {_fmt_t(t['fire_ts']):11s} {t['side'][:1]:1s} {t['entry']:>9.1f} {_fmt_t(r['t_ref']):>11s} {_fmt_t(r['warn']):>11s} "
          f"{_fmt_t(r['trigger_avail']):>11s} {_fmt_t(r['exit_ts']):>11s} {r['exit_price']:>9.1f} {r['r']:>+6.2f} {d['mfe']:>5.2f} "
          f"{r['base_outcome']:>4s} {(r['exit_ts'] - r['warn']) / 60:>8.0f}m {(r['exit_ts'] - r['t_ref']) / 60:>8.0f}m  "
          f"{','.join(r['trigger'])}" + ("  [tie]" if r["tie"] else ""))

    p("\nSECTION 5 -- POTENTIALLY DAMAGED WINNERS (baseline TP closed early by the rule)")
    dw = [(t, d, r) for t, d, r in ctp if r["base_outcome"] == "TP"]
    if not dw:
        p("  none")
    for t, d, r in dw:
        p(f"  {_fmt_t(t['fire_ts'])} {t['side'][:1]}  baseline TP +2.00R (MFE {d['mfe']:.2f}R)  conditioned exit {r['r']:+.2f}R at "
          f"{_fmt_t(r['exit_ts'])}  afterwards: high {r['after_max_r']:+.2f}R, low {r['after_min_r']:+.2f}R, baseline TP at "
          f"{_fmt_t(d['exit_ts'])} ({(d['exit_ts'] - r['exit_ts']) / 60:.0f} min later)  would have reached the 3 ATR TP: YES  "
          f"cost {r['r'] - 2.0:+.2f}R")
    p(f"  damaged winners: {len(dw)} of {btp} baseline TPs; total cost {sum(r['r'] - 2.0 for _, _, r in dw):+.2f}R")

    p("\nSECTION 6 -- RESCUED LOSERS (baseline SL closed by the rule above -1R)")
    rl = [(t, d, r) for t, d, r in ctp if r["base_outcome"] == "SL"]
    for t, d, r in rl:
        p(f"  {_fmt_t(t['fire_ts'])} {t['side'][:1]}  baseline -1.00R  conditioned {r['r']:+.2f}R  improvement {r['r'] + 1.0:+.2f}R  "
          f"(exit {_fmt_t(r['exit_ts'])}, baseline SL {_fmt_t(d['exit_ts'])})")
    pos_ = [x for x in rl if x[2]["r"] > 0]
    p(f"  rescued: {len(rl)} of {bsl} baseline SLs; converted to positive R: {len(pos_)}; "
      f"total improvement {sum(r['r'] + 1 for _, _, r in rl):+.2f}R")

    p("\nSECTION 7 -- the trades that reached +1.5R and eventually hit SL (same numbering as the forensic run)")
    tens = [(t, d, r) for t, d, r in zip(trades, diags, recs) if d["outcome"] == "SL" and d["milestones"].get(m)]
    for n, (t, d, r) in enumerate(tens, 1):
        a = analyze(t, d, m, rows, opens)
        cls = sequence(t, d, a, rows, opens)["class"][:1] if a else "?"
        log = "; ".join(f"{k} {_fmt_t(tm)}" for k, tm in r["log"]) or "no warning"
        if r["outcome"] == "CTP":
            res = (f"CONDITIONED EXIT {r['r']:+.2f}R at {_fmt_t(r['exit_ts'])} on {','.join(r['trigger'])} "
                   f"(improvement {r['r'] + 1:+.2f}R)")
        else:
            res = "rule did NOT fire -> baseline SL -1.00R"
        p(f"  Trade {n:>2} {_fmt_t(t['fire_ts'])} {t['side'][:1]} forensic class {cls}: {res}")
        p(f"            warnings/cancels: {log}")

    p("\nSECTION 8 -- CONCLUSION (facts only)")
    p(f" 1. Baseline SLs rescued (closed above -1R): {len(rl)} of {bsl}; converted to positive R: {len(pos_)}")
    p(f" 2. Baseline winners cut early: {len(dw)} of {btp}")
    p(f" 3. Net R: {bs['net']:+.2f} -> {cs['net']:+.2f}  (difference {cs['net'] - bs['net']:+.2f}R)")
    p(f" 4. Win rate: {100 * bs['wins'] / N:.1f}% -> {100 * cs['wins'] / N:.1f}%")
    p(f" 5. Profit factor: {f(bs['pf'])} -> {f(cs['pf'])}")
    p(f" 6. Trades that triggered the Conditioned TP: {len(ctp)}  (ties resolved in favour of the trigger: "
      f"{sum(1 for _, _, r in ctp if r['tie'])})")
    healthy = [r for t, d, r in zip(trades, diags, recs) if d["outcome"] == "TP" and r["n_warn"] > 0]
    p(f" 7. Baseline winners that showed a 1M warning and were kept as normal TPs: "
      f"{sum(1 for r in healthy if r['outcome'] == 'TP')} of {len(healthy)}")
    p(f" 8. Of the {len(tens)} eventual-SL reversals after +1.5R, the rule fired on {sum(1 for _, _, r in tens if r['outcome'] == 'CTP')}")
    return "\n".join(out)
