"""Forensic WHY analysis of trades after an R milestone (analysis only).

For a chosen milestone (default +1.5R) every trade that reached it is replayed
from that moment to its exit (TP or SL).  Structural events are detected only
from candles that were CLOSED at the time the event is timestamped
("avail" = candle open + timeframe seconds); nothing after the exit is used.

Events (opposite = against the trade's direction):
  M1/M5/M15_opp_BOS / _opp_CHoCH  a close through the last confirmed swing (lr=2)
  LOST_1m / LOST_5m / LOST_15m    a close back through the ORIGINAL breakout level
  LINE_LOST_setup                 a setup-timeframe close back through the projected trendline
  ENGINE_INVALID                  a 1M close beyond the engine's own invalidation level
  H1_opp_TL_break                 an opposite 1H trendline break
  H1_dir_lost                     1H trendline direction stopped agreeing with the trade
Events dated after the exit candle opens are not counted as "before exit".
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Dict, List, Optional

from . import trendline as tl
from .diagnostics import MILESTONES, diagnose_trade
from .gauges import structure_events

SEC = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
BACK = {"1m": 200, "5m": 120, "15m": 80, "1h": 600}
KEYS = ["M1_opp_BOS", "M1_opp_CHoCH", "M5_opp_BOS", "M5_opp_CHoCH", "M15_opp_BOS", "M15_opp_CHoCH",
        "LOST_1m", "LOST_5m", "LOST_15m", "LINE_LOST_setup", "ENGINE_INVALID", "H1_opp_TL_break", "H1_dir_lost"]
ONE_MIN_ONLY = ("M1_opp_BOS", "M1_opp_CHoCH", "LOST_1m", "ENGINE_INVALID")


def _window(rows, opens, tf, t_start, t_end):
    a = bisect_left(opens[tf], t_start)
    b = bisect_right(opens[tf], t_end - SEC[tf])
    return rows[tf][a:b]


def _mins(x):
    return x / 60.0


def _med(x):
    y = sorted(x)
    if not y:
        return None
    k = len(y) // 2
    return float(y[k]) if len(y) % 2 else (y[k - 1] + y[k]) / 2.0


def _fmt_t(ts):
    import datetime
    return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%m-%d %H:%M")


def _dir_at(res, win, k):
    last = None
    for e in res.events:
        if e.index <= k:
            last = e
    if last is None:
        return "NEUTRAL"
    v, c = last.line.value_at_index(k), float(win[k]["close"])
    if last.direction == "LONG" and c > v:
        return "LONG"
    if last.direction == "SHORT" and c < v:
        return "SHORT"
    return "NEUTRAL"


def analyze(trade: dict, diag: dict, m: float, rows, opens) -> Optional[Dict]:
    ms = diag["milestones"].get(m)
    if not ms:
        return None
    side = trade["side"]
    t_ref = ms["reach_ts"] + 60
    t_end = diag["exit_ts"] if diag["exit_ts"] is not None else rows["1m"][-1]["ts"] + 60
    ev: Dict[str, dict] = {}
    aligned: List[dict] = []
    snap: Dict[str, object] = {"bars_fire_to_ref": ms["reach_bar"]}

    # structure events on 1M / 5M / 15M
    for tf, tag in (("1m", "M1"), ("5m", "M5"), ("15m", "M15")):
        win = _window(rows, opens, tf, trade["fire_ts"] - BACK[tf] * SEC[tf], t_end)
        by_ts = {int(c["ts"]): float(c["close"]) for c in win}
        last_before = None
        for e in structure_events(win):
            avail = e["ts"] + SEC[tf]
            if avail > t_end:
                continue
            if avail <= t_ref:
                last_before = e
                continue
            rec = {"avail": avail, "price": by_ts.get(e["ts"]), "level": e["level"], "type": e["type"]}
            if e["direction"] != side:
                ev.setdefault(f"{tag}_opp_{e['type']}", rec)
            else:
                aligned.append({**rec, "label": f"{tag} {e['type']} (continuation)"})
        snap[f"last_{tag}"] = (None if last_before is None else
                               ("aligned " if last_before["direction"] == side else "opposite ") + last_before["type"])

    # original breakout level / projected line / engine invalidation
    lvl, inv = trade["break_level"], trade["invalid_level"]
    beyond = (lambda c, x: c < x) if side == "LONG" else (lambda c, x: c > x)
    for tf in ("1m", "5m", "15m"):
        for c in _window(rows, opens, tf, t_ref - SEC[tf] + 1, t_end):
            if beyond(float(c["close"]), lvl):
                ev.setdefault(f"LOST_{tf}", {"avail": int(c["ts"]) + SEC[tf], "price": float(c["close"])})
                break
    for c in _window(rows, opens, "1m", t_ref - 59, t_end):
        if beyond(float(c["close"]), inv):
            ev["ENGINE_INVALID"] = {"avail": int(c["ts"]) + 60, "price": float(c["close"])}
            break
    bl, stf = trade.get("break_line"), trade["setup_tf"]
    if bl:
        sign = -1.0 if bl["line_kind"] == "upper" else 1.0
        for c in _window(rows, opens, stf, t_ref - SEC[stf] + 1, t_end):
            v = bl["pivot_price"] + sign * bl["slope"] * (int(c["ts"]) - bl["pivot_ts"]) / SEC[stf]
            if beyond(float(c["close"]), v):
                ev["LINE_LOST_setup"] = {"avail": int(c["ts"]) + SEC[stf], "price": float(c["close"])}
                break

    # 1H trendline
    win = _window(rows, opens, "1h", trade["fire_ts"] - BACK["1h"] * 3600, t_end)
    if len(win) > 30:
        res = tl.compute(win)
        wopen = [int(c["ts"]) for c in win]
        k_of = lambda t: bisect_right(wopen, t - 3600) - 1
        for e in res.events:
            if e.direction != side and t_ref < e.ts + 3600 <= t_end:
                ev["H1_opp_TL_break"] = {"avail": e.ts + 3600, "price": e.close}
                break
        k_ref, k_fire = k_of(t_ref), k_of(trade["fire_ts"])
        snap["h1_at_fire"] = _dir_at(res, win, k_fire) if k_fire >= 0 else "NEUTRAL"
        snap["h1_at_ref"] = _dir_at(res, win, k_ref) if k_ref >= 0 else "NEUTRAL"
        if snap["h1_at_ref"] == side:
            for k in range(k_ref + 1, len(win)):
                if wopen[k] + 3600 <= t_end and _dir_at(res, win, k) != side:
                    ev["H1_dir_lost"] = {"avail": wopen[k] + 3600, "price": float(win[k]["close"])}
                    break
        kx = k_of(t_end)
        snap["h1_at_exit"] = _dir_at(res, win, kx) if kx >= 0 else "NEUTRAL"

    px_ref = None
    for c in _window(rows, opens, "1m", t_ref - 59, t_ref):
        px_ref = float(c["close"])
    if px_ref is not None and trade.get("atr"):
        snap["ref_dist_from_level_atr"] = ((px_ref - lvl) if side == "LONG" else (lvl - px_ref)) / trade["atr"]

    ordered = sorted(ev.items(), key=lambda kv: (kv[1]["avail"], KEYS.index(kv[0])))
    first = ordered[0] if ordered else None
    first5 = next(((k, v) for k, v in ordered if k not in ONE_MIN_ONLY), None)
    seq = sorted([(v["avail"], k, v.get("price")) for k, v in ordered] +
                 [(a["avail"], a["label"], a["price"]) for a in aligned])
    return {"t_ref": t_ref, "t_end": t_end, "events": ev, "first": first, "first5": first5, "seq": seq,
            "snap": snap, "aligned_n": len(aligned), "aligned": aligned}


def report(trades: List[dict], rows, opens, m: float = 1.5, csv_path: Optional[str] = None) -> str:
    out: List[str] = []
    p = out.append
    diags = [diagnose_trade(t, rows["1m"]) for t in trades]
    sl = [(t, d) for t, d in zip(trades, diags) if d["outcome"] == "SL"]
    tp = [(t, d) for t, d in zip(trades, diags) if d["outcome"] == "TP"]
    p("=" * 78)
    p(f"FORENSIC WHY ANALYSIS  (milestone +{m}R; analysis only; same trades, same causal engine)")
    p(f"trades={len(trades)} TP={len(tp)} SL={len(sl)}")
    p("Events are timestamped when their candle CLOSED; only events before the exit candle count.")
    p("=" * 78)

    A = {}
    for t, d in sl + tp:
        a = analyze(t, d, m, rows, opens)
        if a:
            A[id(t)] = (t, d, a)
    fails = [(t, d, A[id(t)][2]) for t, d in sl if id(t) in A]
    wins = [(t, d, A[id(t)][2]) for t, d in tp if id(t) in A]

    p(f"\nSECTION 1 -- forensic record of every SL trade that reached +{m}R ({len(fails)} trades)")
    for n, (t, d, a) in enumerate(fails, 1):
        ms = d["milestones"]
        p(f"\nTRADE {n}")
        p(f"  Entry: {t['entry']:.1f}   Side: {t['side']}   FIRE: {_fmt_t(t['fire_ts'])} UTC   ATR: {t['atr']:.1f}")
        p(f"  SL: {t['sl']:.1f}   TP: {t['tp']:.1f}   break level: {t['break_level']:.1f}   setup tf: {t['setup_tf']}")
        p(f"  +{m}R: {_fmt_t(a['t_ref'])}   +1.75R: {_fmt_t(ms[1.75]['reach_ts']) if ms[1.75] else '-'}   "
          f"+1.9R: {_fmt_t(ms[1.9]['reach_ts']) if ms[1.9] else '-'}   MFE: {d['mfe']:.2f}R at {_fmt_t(d['peak_ts'])}")
        p(f"  SL hit: {_fmt_t(d['exit_ts'])}   (+{m}R -> SL: {_mins(a['t_end'] - a['t_ref']):.0f} min)")
        sn = a["snap"]
        p(f"  State at +{m}R: last 1M {sn.get('last_M1')}; 5M {sn.get('last_M5')}; 15M {sn.get('last_M15')}; "
          f"1H trendline dir fire/ref/exit = {sn.get('h1_at_fire')}/{sn.get('h1_at_ref')}/{sn.get('h1_at_exit')}")
        if a["first"]:
            k, v = a["first"]
            p(f"  FIRST FAILURE: {_fmt_t(v['avail'])} UTC  (+{_mins(v['avail'] - a['t_ref']):.0f} min after +{m}R, "
              f"{_mins(a['t_end'] - v['avail']):.0f} min before SL)  {k}  price {v['price']:.1f}")
        else:
            p("  FIRST FAILURE: none -- no structural event before the SL (price decay)")
        if a["first5"] and a["first5"] != a["first"]:
            k, v = a["first5"]
            p(f"  first 5M-or-higher failure: {_fmt_t(v['avail'])}  (+{_mins(v['avail'] - a['t_ref']):.0f} min)  {k}")
        elif not a["first5"]:
            p("  first 5M-or-higher failure: none before the SL")
        p("  SEQUENCE: +%sR" % m)
        for avail, label, price in a["seq"]:
            p(f"     -> {_fmt_t(avail)}  (+{_mins(avail - a['t_ref']):.0f} min)  {label}" + (f"  @ {price:.1f}" if price else ""))
        p("     -> SL")

    def _agg(group):
        n = len(group)
        rows_ = []
        for k in KEYS:
            rows_.append((k, sum(1 for _, _, a in group if k in a["events"])))
        any5 = sum(1 for _, _, a in group if any(k not in ONE_MIN_ONLY for k in a["events"]))
        anyev = sum(1 for _, _, a in group if a["events"])
        return n, rows_, any5, anyev

    p(f"\nSECTION 2 -- events occurring AFTER +{m}R and BEFORE the exit: eventual SLs vs eventual TPs")
    ns, rs, s5, sa = _agg(fails)
    nw, rw, w5, wa = _agg(wins)
    p(f"{'EVENT / CONDITION':32s} {'eventual SL':>12s} {'eventual TP':>12s}")
    p(f"{'trades in group':32s} {ns:>12d} {nw:>12d}")
    for (k, a_), (_, b_) in zip(rs, rw):
        p(f"{k:32s} {a_:>9d}/{ns:<2d} {b_:>9d}/{nw:<2d}")
    p(f"{'any 5M-or-higher failure':32s} {s5:>9d}/{ns:<2d} {w5:>9d}/{nw:<2d}")
    p(f"{'any event at all':32s} {sa:>9d}/{ns:<2d} {wa:>9d}/{nw:<2d}")
    p(f"{'NO structural failure':32s} {ns - sa:>9d}/{ns:<2d} {nw - wa:>9d}/{nw:<2d}")

    p(f"\nSECTION 2b -- state at +{m}R (before any failure)")
    for name, grp in (("eventual SL", fails), ("eventual TP", wins)):
        def cnt(key, val):
            return sum(1 for _, _, a in grp if (a["snap"].get(key) or "").startswith(val))
        h1 = sum(1 for t, _, a in grp if a["snap"].get("h1_at_ref") == t["side"])
        dist = [a["snap"]["ref_dist_from_level_atr"] for _, _, a in grp if "ref_dist_from_level_atr" in a["snap"]]
        p(f"  {name} (n={len(grp)}): last 1M event aligned {cnt('last_M1','aligned')}, last 5M aligned {cnt('last_M5','aligned')}, "
          f"last 15M aligned {cnt('last_M15','aligned')}, 1H trendline agrees {h1}, "
          f"median bars fire->+{m}R {_med([a['snap']['bars_fire_to_ref'] for _, _, a in grp])}, "
          f"median distance from breakout level {(_med(dist) or 0):.2f} ATR")

    p("\nSECTION 3 -- how the losing population disappears: first failure after each milestone")
    for mm in MILESTONES[:6]:
        grp = []
        for t, d in sl:
            if d["milestones"].get(mm):
                a = analyze(t, d, mm, rows, opens)
                if a:
                    grp.append((t, d, a))
        cnts: Dict[str, int] = {}
        gaps1, gaps2 = [], []
        for t, d, a in grp:
            key = a["first"][0] if a["first"] else "NONE (no structural event before SL)"
            cnts[key] = cnts.get(key, 0) + 1
            if a["first"]:
                gaps1.append(_mins(a["first"][1]["avail"] - a["t_ref"]))
                gaps2.append(_mins(a["t_end"] - a["first"][1]["avail"]))
        first5 = sum(1 for _, _, a in grp if a["first5"])
        p(f"  +{mm}R: {len(grp)} eventual SLs; a 5M-or-higher failure occurred before the SL in {first5}; "
          f"median min to first failure {_med(gaps1) if gaps1 else '-'}, failure->SL {_med(gaps2) if gaps2 else '-'}")
        for k, v in sorted(cnts.items(), key=lambda x: -x[1]):
            p(f"        first failure = {k}: {v}")

    p("\nSECTION 4 -- SL trades (reached the milestone) with NO structural failure before the SL, or only 1M noise")
    for n, (t, d, a) in enumerate(fails, 1):
        if not a["events"]:
            p(f"  trade {n} ({_fmt_t(t['fire_ts'])} {t['side']}): no event at all -> price decay")
        elif not a["first5"]:
            p(f"  trade {n} ({_fmt_t(t['fire_ts'])} {t['side']}): only 1M-level events: {', '.join(a['events'])}")

    out.extend(sequence_report(fails, wins, rows, opens, m))

    if csv_path:
        import csv
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["fire_time", "side", "outcome", "ref_time"] + KEYS + ["first_failure", "first_5m_plus"])
            for t, d in zip(trades, diags):
                a = analyze(t, d, m, rows, opens)
                if not a:
                    continue
                w.writerow([_fmt_t(t["fire_ts"]), t["side"], d["outcome"], _fmt_t(a["t_ref"])] +
                           [(_fmt_t(a["events"][k]["avail"]) if k in a["events"] else "") for k in KEYS] +
                           [a["first"][0] if a["first"] else "", a["first5"][0] if a["first5"] else ""])
        p(f"\nper-trade events written to {csv_path}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# What happens AFTER the first 1M opposite CHoCH ("early warning")
# ---------------------------------------------------------------------------
FIVE_PLUS = ("M5_opp_BOS", "M5_opp_CHoCH", "M15_opp_BOS", "M15_opp_CHoCH", "LOST_5m", "LOST_15m",
             "LINE_LOST_setup", "ENGINE_INVALID", "H1_opp_TL_break", "H1_dir_lost")


def _mom(rows, opens, tf, t, n, atr, s):
    k = bisect_right(opens[tf], t - SEC[tf]) - 1
    if k - n < 0 or not atr:
        return None
    return s * (float(rows[tf][k]["close"]) - float(rows[tf][k - n]["close"])) / atr


def sequence(trade: dict, diag: dict, a: dict, rows, opens) -> Dict:
    """Track momentum / continuation / structure after the +m R point and after the first 1M CHoCH."""
    side, entry, risk, atr = trade["side"], trade["entry"], trade["_risk"], trade["atr"]
    s = 1.0 if side == "LONG" else -1.0
    t_ref, t_end = a["t_ref"], a["t_end"]
    exit_is_sl = diag["outcome"] == "SL"
    path = []                                   # (avail, favR, advR) of every 1M candle after entry up to exit
    for c in rows["1m"][trade["i"] + 1:]:
        avail = int(c["ts"]) + 60
        if int(c["ts"]) > t_end:
            break
        if exit_is_sl and int(c["ts"]) == t_end:
            continue                            # SL candle: nothing in it is credited (SL-first rule)
        h, l = float(c["high"]), float(c["low"])
        fav = ((h - entry) if s > 0 else (entry - l)) / risk
        adv = ((l - entry) if s > 0 else (entry - h)) / risk
        path.append((avail, fav, adv))
    after_ref = [x for x in path if x[0] > t_ref]
    out: Dict = {"mfe_after_ref": max([x[1] for x in after_ref] or [0.0]),
                 "low_after_ref": (-1.0 if exit_is_sl else min([x[2] for x in after_ref] or [1.5])),
                 "minutes_after_ref": (t_end - t_ref) / 60.0}
    w = a["events"].get("M1_opp_CHoCH")
    out["warning"] = bool(w)
    out["mom1_ref"] = _mom(rows, opens, "1m", t_ref, 10, atr, s)
    out["mom5_ref"] = _mom(rows, opens, "5m", t_ref, 3, atr, s)
    if not w:
        out["class"] = "N: no 1M CHoCH warning -- continued to the exit"
        return out
    tw = w["avail"]
    pre_peak = max([x[1] for x in path if x[0] <= tw] or [0.0])
    new = next((x for x in path if x[0] > tw and x[1] > pre_peak + 1e-9), None)
    win_end = new[0] if new else t_end
    seg = [x for x in path if tw < x[0] <= win_end]
    out.update({"warn_min": (tw - t_ref) / 60.0, "pre_peak": pre_peak,
                "new_extreme": bool(new), "min_to_new_extreme": ((new[0] - tw) / 60.0 if new else None),
                "level_before_new": (min([x[2] for x in seg]) if seg else None),
                "pullback_depth": (pre_peak - min([x[2] for x in seg]) if seg else 0.0),
                "peak_after_warning": diag["peak_ts"] is not None and diag["peak_ts"] + 60 > tw,
                "mom1_warn": _mom(rows, opens, "1m", tw, 10, atr, s), "mom5_warn": _mom(rows, opens, "5m", tw, 3, atr, s)})
    m1s = [_mom(rows, opens, "1m", x[0], 10, atr, s) for x in seg if x[0] <= tw + 1800]
    m1s = [x for x in m1s if x is not None]
    out["mom1_min_after_warn_30m"] = min(m1s) if m1s else None
    between = {k: v for k, v in a["events"].items() if k in FIVE_PLUS and tw < v["avail"] <= win_end}
    out["between"] = sorted(between, key=lambda k: between[k]["avail"])
    out["aligned_after_warn"] = sum(1 for x in a["aligned"] if tw < x["avail"] <= win_end and x["label"].startswith(("M1", "M5")))
    if new:
        out["class"] = ("A2: warning -> 5M+ structure damaged -> still made a new extreme" if between
                        else "A: warning -> pullback -> 5M+ structure intact -> new extreme")
    else:
        out["class"] = ("B: warning -> no new extreme -> 5M+ structure failed -> SL" if between
                        else "C: warning -> no new extreme -> no 5M+ failure -> decay to SL")
    return out


def sequence_report(fails, wins, rows, opens, m) -> List[str]:
    out: List[str] = []
    p = out.append
    S = {}
    for grp in (fails, wins):
        for t, d, a in grp:
            S[id(t)] = sequence(t, d, a, rows, opens)

    def f(x, nd=2):
        return "-" if x is None else f"{x:.{nd}f}"

    p(f"\nSECTION 5 -- what happened AFTER the +{m}R point in each eventual-SL trade")
    p(f"{'#':>2} {'fire (UTC)':11s} {'S':>1} {'warn+min':>8} {'preWpeak':>8} {'newExt?':>7} {'pullback':>8} "
      f"{'lowBefNew':>9} {'alignAtt':>8} {'5M+ before new/SL':30s} {'mom1 ref/warn/min30':>22}  class")
    for n, (t, d, a) in enumerate(fails, 1):
        q = S[id(t)]
        p(f"{n:>2} {_fmt_t(t['fire_ts']):11s} {t['side'][:1]:>1} {f(q.get('warn_min'), 0):>8} {f(q.get('pre_peak')):>8} "
          f"{('yes' if q.get('new_extreme') else 'no'):>7} {f(q.get('pullback_depth')):>8} {f(q.get('level_before_new')):>9} "
          f"{q.get('aligned_after_warn', 0):>8} {','.join(q.get('between', [])) or '-':30s} "
          f"{f(q.get('mom1_ref'))}/{f(q.get('mom1_warn'))}/{f(q.get('mom1_min_after_warn_30m')):>6}  {q['class']}")
    p("  (pullback = pre-warning peak R minus lowest R before a new extreme, or before the SL; momentum = 10-bar 1M net move in ATR)")

    p(f"\nSECTION 6 -- eventual SL vs eventual TP after +{m}R")

    def col(group, key):
        return [S[id(t)][key] for t, _, _ in group if S[id(t)].get(key) is not None]

    p(f"{'':52s} {'eventual SL':>12s} {'eventual TP':>12s}")
    p(f"{'trades':52s} {len(fails):>12d} {len(wins):>12d}")
    p(f"{'had a 1M opposite CHoCH (warning)':52s} {sum(S[id(t)]['warning'] for t, _, _ in fails):>12d} {sum(S[id(t)]['warning'] for t, _, _ in wins):>12d}")
    for name, key in ((f"median minutes after +{m}R until exit", "minutes_after_ref"),
                      (f"median max favourable R after +{m}R", "mfe_after_ref"),
                      (f"median lowest R after +{m}R", "low_after_ref"),
                      (f"median momentum(1M) at +{m}R", "mom1_ref")):
        p(f"{name:52s} {f(_med(col(fails, key))):>12s} {f(_med(col(wins, key))):>12s}")
    fw = [(t, d, a) for t, d, a in fails if S[id(t)]["warning"]]
    ww = [(t, d, a) for t, d, a in wins if S[id(t)]["warning"]]
    p("  -- among trades that HAD the 1M CHoCH warning --")
    for name, key in (("median minutes: +R point -> warning", "warn_min"), ("median pre-warning peak (R)", "pre_peak"),
                      ("median pullback depth after warning (R)", "pullback_depth"),
                      ("median lowest R before new extreme / SL", "level_before_new"),
                      ("median momentum(1M) at warning", "mom1_warn"),
                      ("median min momentum(1M) in 30 min after warning", "mom1_min_after_warn_30m"),
                      ("median aligned 1M/5M structure attempts after warning", "aligned_after_warn")):
        p(f"{name:52s} {f(_med(col(fw, key))):>12s} {f(_med(col(ww, key))):>12s}")
    p(f"{'made a new extreme after the warning':52s} {sum(S[id(t)]['new_extreme'] for t, _, _ in fw):>9d}/{len(fw):<2d} {sum(S[id(t)]['new_extreme'] for t, _, _ in ww):>9d}/{len(ww):<2d}")
    p(f"{'MFE occurred after the warning':52s} {sum(S[id(t)]['peak_after_warning'] for t, _, _ in fw):>9d}/{len(fw):<2d} {sum(S[id(t)]['peak_after_warning'] for t, _, _ in ww):>9d}/{len(ww):<2d}")
    for key in FIVE_PLUS:
        a_ = sum(key in S[id(t)].get("between", []) for t, _, _ in fw)
        b_ = sum(key in S[id(t)].get("between", []) for t, _, _ in ww)
        p(f"{'  ' + key + ' between warning and new extreme/exit':52s} {a_:>9d}/{len(fw):<2d} {b_:>9d}/{len(ww):<2d}")

    p("\nSECTION 7 -- sequence class (after the 1M CHoCH warning) by eventual outcome")
    classes = sorted({S[id(t)]["class"] for grp in (fails, wins) for t, _, _ in grp})
    p(f"{'class':78s} {'SL':>4s} {'TP':>4s}")
    for c in classes:
        p(f"{c:78s} {sum(S[id(t)]['class'] == c for t, _, _ in fails):>4d} {sum(S[id(t)]['class'] == c for t, _, _ in wins):>4d}")

    p("\nSECTION 8 -- winners that DID show the warning (each row is one trade)")
    for t, d, a in ww:
        q = S[id(t)]
        p(f"  {_fmt_t(t['fire_ts'])} {t['side'][:1]}  warn +{q['warn_min']:.0f}m  pullback {q['pullback_depth']:.2f}R  "
          f"low {f(q['level_before_new'])}R  new extreme after {f(q['min_to_new_extreme'], 0)}m  5M+ between: "
          f"{','.join(q['between']) or '-'}  aligned attempts {q['aligned_after_warn']}  class {q['class'][:2]}")
    return out


# ---------------------------------------------------------------------------
# WHY did the SL trades fail?  (all SL trades vs all TP trades, from FIRE)
# ---------------------------------------------------------------------------
def _fisher_p(a, b, c, d):
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]] (pure python)."""
    from math import comb
    r1, r2, c1, n = a + b, c + d, a + c, a + b + c + d
    def pr(x):
        return comb(r1, x) * comb(r2, c1 - x) / comb(n, c1)
    p0 = pr(a)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    return min(1.0, sum(pr(x) for x in range(lo, hi + 1) if pr(x) <= p0 * (1 + 1e-9)))


def _from_fire(trade, diag):
    d = dict(diag)
    d["milestones"] = {0.0: {"reach_ts": trade["fire_ts"] - 60, "reach_bar": 0}}
    return d


def why_sl_report(trades: List[dict], rows, opens, csv_path: Optional[str] = None) -> str:
    out: List[str] = []
    p = out.append
    trades = [t for t in trades if t.get("exit")]
    diags = [diagnose_trade(t, rows["1m"]) for t in trades]
    S, W = [], []
    for t, d in zip(trades, diags):
        a = analyze(t, _from_fire(t, d), 0.0, rows, opens)
        rec = {"t": t, "d": d, "a": a}
        (S if d["outcome"] == "SL" else W).append(rec)
    p("=" * 78)
    p(f"WHY THE SL TRADES FAILED  ({len(S)} SL vs {len(W)} TP; every measure is causal from FIRE; analysis only)")
    p("=" * 78)

    # ---- 1. how fast / how far
    p("\nSECTION 1 -- how far did the SL trades ever get, and how fast did they die")
    mins = lambda r: (r["a"]["t_end"] - r["t"]["fire_ts"]) / 60.0
    ms_ = sorted(mins(r) for r in S)
    mw_ = sorted(mins(r) for r in W)
    q = lambda x, f: (x[min(len(x) - 1, int(f * len(x)))] if x else None)
    fm = lambda v, nd=0: "-" if v is None else f"{v:.{nd}f}"
    p(f"  minutes FIRE -> exit   SL: median {fm(_med(ms_))}  25% {fm(q(ms_, .25))}  75% {fm(q(ms_, .75))}   |   "
      f"TP: median {fm(_med(mw_))}  25% {fm(q(mw_, .25))}  75% {fm(q(mw_, .75))}")
    edges = [(-9, .25), (.25, .5), (.5, 1.0), (1.0, 1.5), (1.5, 2.0)]
    p(f"  {'peak R reached before the SL':30s} {'count':>6s} {'% of SL':>8s} {'median min to SL':>17s}")
    for lo, hi in edges:
        g = [r for r in S if lo <= r["d"]["mfe"] < hi]
        p(f"  {('<%.2fR' % hi) if lo < 0 else ('%.2f-<%.2fR' % (lo, hi)):30s} {len(g):>6d} {100 * len(g) / max(1, len(S)):>7.1f}% {fm(_med([mins(r) for r in g])):>17s}")
    early = [r for r in S if mins(r) <= 20]
    p(f"  SL hit within 20 min of FIRE: {len(early)} ({100 * len(early) / len(S):.0f}% of SL); within 60 min: {sum(1 for r in S if mins(r) <= 60)}")

    # ---- 2. taxonomy
    p("\nSECTION 2 -- mechanism of failure (each SL trade in exactly one bucket, first matching row wins)")
    LEVEL = ("LOST_5m", "LOST_15m", "LINE_LOST_setup")
    FIVE = ("M5_opp_BOS", "M5_opp_CHoCH", "M15_opp_BOS", "M15_opp_CHoCH")
    tax: Dict[str, List[dict]] = {}
    for r in S:
        mfe, ev, mn = r["d"]["mfe"], r["a"]["events"], mins(r)
        if mn <= 20:
            k = "1. spike stop-out (SL within 20 min of FIRE)"
        elif mfe >= 1.0:
            k = "6. worked well (peak >= 1.0R) then failed"
        elif mfe >= 0.5:
            k = "5. worked a little (peak 0.5-1.0R) then failed"
        elif any(x in ev for x in LEVEL):
            k = "2. never worked; price closed back through the breakout level"
        elif any(x in ev for x in FIVE):
            k = "3. never worked; 5M/15M structure reversed against it"
        else:
            k = "4. never worked; drifted to the stop (no 5M+ structure event)"
        tax.setdefault(k, []).append(r)
    for k in sorted(tax):
        g = tax[k]
        p(f"  {k:70s} {len(g):>3d}  ({100 * len(g) / len(S):.0f}%)  median {fm(_med([mins(x) for x in g]))} min to SL")

    # ---- 3. entry state
    p("\nSECTION 3 -- entry-time conditions: eventual SL vs eventual TP  (Fisher exact, UNADJUSTED for many looks)")
    def feats(r):
        t, a = r["t"], r["a"]
        sn = a["snap"]
        dist = (t["entry"] - t["break_level"]) / t["atr"] * (1 if t["side"] == "LONG" else -1)
        since = (t["fire_ts"] - t["setup_ts"] - SEC[t["setup_tf"]]) / 60.0
        h = (t["fire_ts"] % 86400) // 14400 * 4
        return {
            "SHORT": t["side"] == "SHORT",
            "1H trendline agrees at FIRE": sn.get("h1_at_fire") == t["side"],
            "last 5M structure event aligned": (sn.get("last_M5") or "").startswith("aligned"),
            "last 15M structure event aligned": (sn.get("last_M15") or "").startswith("aligned"),
            "last 1M structure event aligned": (sn.get("last_M1") or "").startswith("aligned"),
            "confidence >= 0.70": t["conf"] >= 0.7,
            "CHoCH/BOS gauge = BOS": t["struct"] == "BOS",
            "CHoCH/BOS gauge = NONE": t["struct"] == "NONE",
            "master 1D/4H/1H fully aligned": t["align"] == "ALIGNED",
            "entry within 0.5 ATR of break level": abs(dist) <= 0.5,
            "entry > 1.0 ATR beyond break level": dist > 1.0,
            ">60 min between break and FIRE": since > 60,
            "stop < 0.25% of price": t["stop_pct"] < 0.25,
            "FIRE 20:00-24:00 UTC": h == 20,
            "FIRE 00:00-04:00 UTC": h == 0,
        }
    FS = [feats(r) for r in S]
    FW = [feats(r) for r in W]
    p(f"  {'condition':38s} {'SL':>7s} {'TP':>7s} {'win% if present':>16s} {'win% if absent':>15s} {'p':>7s}")
    rowsf = []
    for k in FS[0]:
        a_ = sum(f[k] for f in FS); c_ = sum(f[k] for f in FW)
        b_, d_ = len(FS) - a_, len(FW) - c_
        wp = 100 * c_ / (a_ + c_) if a_ + c_ else 0
        wa = 100 * d_ / (b_ + d_) if b_ + d_ else 0
        rowsf.append((k, a_, c_, wp, wa, _fisher_p(a_, b_, c_, d_)))
    for k, a_, c_, wp, wa, pv in sorted(rowsf, key=lambda x: x[5]):
        p(f"  {k:38s} {a_:>4d}/{len(FS):<2d} {c_:>4d}/{len(FW):<2d} {wp:>15.0f}% {wa:>14.0f}% {pv:>7.3f}")
    p(f"  (with {len(rowsf)} conditions tested, expect about {len(rowsf) * 0.05:.1f} at p<0.05 by chance alone)")

    # ---- 4. early behaviour after FIRE, exposure matched
    p("\nSECTION 4 -- behaviour in the first minutes after FIRE, trades still open at that time (fair exposure)")
    for T in (15, 30, 60):
        sl_o = [r for r in S if mins(r) > T]
        tp_o = [r for r in W if mins(r) > T]
        def cnt(g, keys):
            return sum(1 for r in g if any(k in r["a"]["events"] and (r["a"]["events"][k]["avail"] - r["t"]["fire_ts"]) / 60 <= T for k in keys))
        p(f"  still open at {T:>2d} min: SL {len(sl_o):>2d} / TP {len(tp_o):>2d}   "
          f"opposite 1M CHoCH by then {cnt(sl_o, ('M1_opp_CHoCH',)):>2d}/{len(sl_o):<2d} vs {cnt(tp_o, ('M1_opp_CHoCH',)):>2d}/{len(tp_o):<2d};  "
          f"opposite 5M {cnt(sl_o, ('M5_opp_BOS', 'M5_opp_CHoCH')):>2d}/{len(sl_o):<2d} vs {cnt(tp_o, ('M5_opp_BOS', 'M5_opp_CHoCH')):>2d}/{len(tp_o):<2d};  "
          f"level lost {cnt(sl_o, LEVEL + ('LOST_1m',)):>2d}/{len(sl_o):<2d} vs {cnt(tp_o, LEVEL + ('LOST_1m',)):>2d}/{len(tp_o):<2d}")

    # ---- 5. noise or wrong direction?  what did price do AFTER the stop
    p("\nSECTION 5 -- after the stop: wrong direction, or stopped by noise?  (diagnostic only; no rule)")
    m1 = rows["1m"]
    res = {"recovered to the TP level before a further -1R": 0, "kept going against (hit -2R first)": 0, "chopped (neither within 6h)": 0}
    for r in S:
        t, d = r["t"], r["d"]
        s = 1 if t["side"] == "LONG" else -1
        k0 = next((k for k in range(t["i"] + 1, len(m1)) if int(m1[k]["ts"]) == d["exit_ts"]), None)
        outcome = "chopped (neither within 6h)"
        if k0 is not None:
            for c in m1[k0 + 1:k0 + 361]:
                lo = ((float(c["low"]) - t["entry"]) if s > 0 else (t["entry"] - float(c["high"]))) / t["_risk"]
                hi = ((float(c["high"]) - t["entry"]) if s > 0 else (t["entry"] - float(c["low"]))) / t["_risk"]
                if lo <= -2.0:
                    outcome = "kept going against (hit -2R first)"; break
                if hi >= 2.0:
                    outcome = "recovered to the TP level before a further -1R"; break
        res[outcome] += 1
    for k, v in res.items():
        p(f"  {k:52s} {v:>3d}  ({100 * v / len(S):.0f}% of SL)")
    mae = sorted(r["d"]["mae"] for r in W)
    p(f"  winners' worst drawdown before TP (R): median {fm(_med(mae), 2)}; >=0.5R in {sum(1 for x in mae if x >= 0.5)}/{len(mae)}; >=0.8R in {sum(1 for x in mae if x >= 0.8)}/{len(mae)}")

    if csv_path:
        import csv
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["fire_time", "side", "outcome", "peak_R", "minutes_to_exit", "bucket"] + list(FS[0]))
            inv = {id(r): k for k, g in tax.items() for r in g}
            for r, ft in list(zip(S, FS)) + list(zip(W, FW)):
                w.writerow([_fmt_t(r["t"]["fire_ts"]), r["t"]["side"], r["d"]["outcome"], round(r["d"]["mfe"], 3),
                            round(mins(r), 1), inv.get(id(r), "")] + [int(v) for v in ft.values()])
        p(f"\nper-trade rows written to {csv_path}")
    return "\n".join(out)
