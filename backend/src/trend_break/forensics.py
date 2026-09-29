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
            "snap": snap, "aligned_n": len(aligned)}


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
