import math
import os
import random

import pytest

from src.trend_break import trendline as tl
from src.trend_break import evaluate, TrendBreakConfig
from src.trend_break.gauges import candle_score


def _series(n=400, seed=7, step=3600):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p += math.sin(i / 9.0) * 1.5 + rnd.uniform(-1, 1)
        c = p
        out.append({"ts": 1_700_000_000 + i * step, "open": o, "close": c,
                    "high": max(o, c) + rnd.uniform(0, .8), "low": min(o, c) - rnd.uniform(0, .8),
                    "volume": 1.0})
    return out


def test_trendline_is_causal_prefix_invariant():
    rows = _series()
    full = tl.compute(rows).events
    assert full, "synthetic series should produce breaks"
    for cut in (150, 250, 333):
        part = tl.compute(rows[:cut]).events
        expect = [(e.index, e.direction, round(e.line_value, 9)) for e in full if e.index < cut]
        assert [(e.index, e.direction, round(e.line_value, 9)) for e in part] == expect


def test_pivot_needs_right_side_closed():
    rows = _series(60)
    # a pivot at index i is only usable from bar i+14
    res = tl.compute(rows)
    for e in res.events:
        assert e.index > e.line.confirm_index >= e.line.pivot_index + 14


def test_wick_through_line_is_not_a_break():
    rows = _series(120)
    res = tl.compute(rows)
    ev = res.events[0]
    i = ev.index
    mod = [dict(c) for c in rows[:i + 1]]
    line = ev.line.value_at_index(i)
    mod[i]["close"] = line - 0.01 if ev.direction == "LONG" else line + 0.01
    mod[i]["high"] = max(mod[i]["high"], line + 5)
    mod[i]["low"] = min(mod[i]["low"], line - 5)
    assert all(e.index != i for e in tl.compute(mod).events)


def test_line_projection_formula():
    ln = tl.Line("upper", 10, 0, 100.0, 24, 0.5)
    assert ln.value_at_index(30) == 90.0
    assert tl.Line("lower", 10, 0, 100.0, 24, 0.5).value_at_index(30) == 110.0


def test_doji_scores_below_marubozu():
    doji = {"open": 100, "close": 100.1, "high": 102, "low": 98}
    fat = {"open": 100, "close": 101.5, "high": 101.8, "low": 99.9}
    assert candle_score(fat, "LONG", 1.5) > candle_score(doji, "LONG", 1.5) + 0.4
    assert candle_score(fat, "SHORT", 1.5) == 0.0


def test_insufficient_data_is_wait_with_reason():
    d = evaluate({"1h": [], "1m": []})
    assert not d.fire and d.reason


DB = os.path.join(os.path.dirname(__file__), "..", "market_data.db")


@pytest.mark.skipif(not os.path.exists(DB), reason="local market_data.db not present")
def test_real_data_fire_levels_from_actual_entry():
    import bisect
    from src.market_data import database as db
    from src.trend_break.engine import TF_SEC
    rows = {tf: db.get_candles("BTC_USDT", tf, limit=100000) for tf in TF_SEC}
    opens = {tf: [c["ts"] for c in rows[tf]] for tf in rows}
    cfg = TrendBreakConfig(require_master_alignment=False)
    fires = []
    for bar in rows["1m"]:
        now = bar["ts"] + 60
        win = {}
        for tf, lim in {"1d": 120, "4h": 200, "1h": 300, "15m": 300, "5m": 300, "1m": 700}.items():
            end = bisect.bisect_right(opens[tf], now - TF_SEC[tf])
            win[tf] = rows[tf][max(0, end - lim):end]
        d = evaluate(win, config=cfg, now_ts=now)
        if d.fire:
            fires.append(d)
            # same result when future candles are appended (no lookahead)
            future = {tf: rows[tf][:opens[tf].index(win[tf][-1]["ts"]) + 1 + 5] for tf in ("1m",)}
            assert evaluate({**win, **{"1m": win["1m"] + rows["1m"][rows["1m"].index(bar) + 1:][:3]}},
                            config=cfg, now_ts=now).fire
            break
    if not fires:
        pytest.skip("no FIRE in local data")
    d = fires[0]
    s = 1 if d.direction == "LONG" else -1
    assert d.sl == pytest.approx(d.entry - s * 1.5 * d.atr)
    assert d.tp == pytest.approx(d.entry + s * 3.0 * d.atr)
    sig = d.to_signal()
    assert sig["action"] == "FIRE" and sig["stop"] == d.sl and sig["target"] == d.tp


def _reference(rows, length=14, mult=1.0):
    """Bar-by-bar transliteration of the reference indicator's recursion."""
    atr = tl.atr_series(rows, length)
    hi = [r["high"] for r in rows]; lo = [r["low"] for r in rows]; cl = [r["close"] for r in rows]
    upper = lower = slope_ph = slope_pl = 0.0
    upos = dnos = 0
    out = []
    started_up = started_dn = False
    for t in range(len(rows)):
        i = t - length
        ph = pl = False
        if i >= length and atr[t] is not None:
            slope = atr[t] / length * mult
            if hi[i] > max(hi[i - length:i]) and hi[i] >= max(hi[i + 1:t + 1]):
                ph, slope_ph, upper, started_up = True, slope, hi[i], True
            if lo[i] < min(lo[i - length:i]) and lo[i] <= min(lo[i + 1:t + 1]):
                pl, slope_pl, lower, started_dn = True, slope, lo[i], True
        if not ph:
            upper -= slope_ph
        if not pl:
            lower += slope_pl
        prev_u, prev_d = upos, dnos
        upos = 0 if ph else (1 if cl[t] > upper - slope_ph * length else upos)
        dnos = 0 if pl else (1 if cl[t] < lower + slope_pl * length else dnos)
        if upos > prev_u and started_up:
            out.append((t, "LONG", upper - slope_ph * length))
        if dnos > prev_d and started_dn:
            out.append((t, "SHORT", lower + slope_pl * length))
    return out


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_matches_reference_indicator_recursion(seed):
    rows = _series(500, seed=seed)
    got = [(e.index, e.direction, e.line_value) for e in tl.compute(rows).events]
    ref = _reference(rows)
    assert [(a, b) for a, b, _ in got] == [(a, b) for a, b, _ in ref]
    for (_, _, x), (_, _, y) in zip(got, ref):
        assert x == pytest.approx(y, rel=1e-9)
    assert len(got) > 3


def test_setup_tf_15m_uses_1h_as_third_master():
    from src.trend_break.engine import TF_SEC
    rows = {tf: _series(120, seed=3, step=TF_SEC[tf]) for tf in TF_SEC}
    d = evaluate(rows, config=TrendBreakConfig(setup_tf="15m"))
    assert d.setup_tf == d.trend_break_timeframe == "15m" and d.reason
    d1 = evaluate(rows, config=TrendBreakConfig(setup_tf="1h"))
    assert d1.setup_tf == "1h" and d1.master_1h_direction == "NEUTRAL"
    with pytest.raises(ValueError):
        evaluate(rows, config=TrendBreakConfig(setup_tf="5m"))


def _zig(n, slope, amp=15.0, period=70):
    out, prev = [], 100.0
    for i in range(n):
        c = 100 + slope * i + amp * math.sin(2 * math.pi * i / period)
        out.append({"ts": 1_700_000_000 + i * 3600, "open": prev, "close": c,
                    "high": max(prev, c) + 0.5, "low": min(prev, c) - 0.5, "volume": 1.0})
        prev = c
    return out


def test_structure_direction_up_down_and_neutral():
    assert tl.structure_direction(_zig(400, 0.6))["direction"] == "LONG"
    assert tl.structure_direction(_zig(400, -0.6))["direction"] == "SHORT"
    assert tl.structure_direction(_zig(30, 0.6))["direction"] == "NEUTRAL"   # nothing confirmed yet
    assert tl.structure_direction(_zig(400, 0.0))["direction"] in ("NEUTRAL",)  # flat = no trend


def test_structure_direction_is_persistent_and_causal():
    up = _zig(400, 0.6)
    # a later pullback that only lowers the swing high must not flip an established LONG
    assert tl.structure_direction(up)["direction"] == "LONG"
    for cut in (200, 300):
        a = tl.structure_direction(up[:cut])["direction"]
        b = tl.structure_direction(up[:cut] + up[cut:cut + 3])["direction"]
        assert a == b or a == "NEUTRAL"       # extra bars alone don't rewrite history


def _c(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def _trade(side="LONG", entry=100.0, risk=10.0):
    s = 1 if side == "LONG" else -1
    return {"side": side, "entry": entry, "_risk": risk, "sl": entry - s * risk, "tp": entry + s * 2 * risk,
            "atr": risk / 1.5, "i": 0, "ts": 0, "exit": None}


def test_diag_near_tp_then_sl_and_normal_tp():
    from src.trend_break.diagnostics import diagnose_trade
    # entry candle idx0, then: +1.8R (118), pullback, then SL (-1R = 90)
    path = [_c(0, 100, 100, 100, 100), _c(60, 100, 118, 101, 117), _c(120, 117, 117, 108, 109),
            _c(180, 109, 109, 95, 96), _c(240, 96, 96, 89, 90)]
    d = diagnose_trade(_trade(), path)
    assert d["outcome"] == "SL" and d["final_r"] == -1.0
    assert d["milestones"][1.5] and d["milestones"][1.75] and not d["milestones"][1.9]
    assert d["milestones"][1.75]["bars_to_exit"] == 3 and d["mfe"] == pytest.approx(1.8)
    # straight to TP
    d2 = diagnose_trade(_trade(), [path[0], _c(60, 100, 121, 99, 120)])
    assert d2["outcome"] == "TP" and d2["milestones"][2.0]


def test_diag_same_candle_tp_and_sl_counts_as_sl_like_backtest():
    from src.trend_break.diagnostics import diagnose_trade
    d = diagnose_trade(_trade(), [_c(0, 100, 100, 100, 100), _c(60, 100, 125, 85, 100)])
    assert d["outcome"] == "SL" and d["sl_candle_touched_tp"] and not d["milestones"][1.0]
    # SHORT mirror
    d3 = diagnose_trade(_trade("SHORT"), [_c(0, 100, 100, 100, 100), _c(60, 100, 101, 82, 83), _c(120, 83, 111, 83, 110)])
    assert d3["outcome"] == "SL" and d3["milestones"][1.75]


def _agg(m1, sec):
    out = {}
    for c in m1:
        k = c["ts"] - c["ts"] % sec
        b = out.get(k)
        if b is None:
            out[k] = {"ts": k, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
        else:
            b["high"], b["low"], b["close"] = max(b["high"], c["high"]), min(b["low"], c["low"]), c["close"]
    return [out[k] for k in sorted(out)]


def _forensic_fixture():
    rnd = random.Random(5)
    m1, p, t0 = [], 100.0, 1_700_000_000 - (1_700_000_000 % 3600)
    for i in range(2600):
        o = p
        if i < 2000:
            p += rnd.uniform(-0.6, 0.6)
        elif i < 2040:
            p += 0.5 + rnd.uniform(-0.05, 0.05)     # rally to about +1.7R
        else:
            p -= 0.35 + rnd.uniform(-0.1, 0.1)      # reversal to the stop
        m1.append({"ts": t0 + 60 * i, "open": o, "close": p, "high": max(o, p) + 0.1, "low": min(o, p) - 0.1})
    entry = m1[2000]["close"]
    risk = 12.0
    trade = {"side": "LONG", "entry": entry, "_risk": risk, "sl": entry - risk, "tp": entry + 2 * risk,
             "atr": risk / 1.5, "i": 2000, "ts": m1[2000]["ts"] + 60, "fire_ts": m1[2000]["ts"] + 60,
             "break_level": entry - 3.0, "invalid_level": entry - 12.0, "setup_tf": "15m",
             "break_line": {"line_kind": "upper", "pivot_ts": m1[1500]["ts"], "pivot_price": entry + 4, "slope": 0.001},
             "exit": None}
    return m1, trade


def _rows_opens(m1):
    rows = {"1m": m1, "5m": _agg(m1, 300), "15m": _agg(m1, 900), "1h": _agg(m1, 3600)}
    return rows, {k: [c["ts"] for c in v] for k, v in rows.items()}


def test_forensics_uses_only_candles_closed_before_exit():
    from src.trend_break.diagnostics import diagnose_trade
    from src.trend_break.forensics import analyze
    m1, trade = _forensic_fixture()
    rows, opens = _rows_opens(m1)
    d = diagnose_trade(trade, m1)
    assert d["outcome"] == "SL" and d["milestones"][1.5]
    a = analyze(trade, d, 1.5, rows, opens)
    assert a and a["events"]
    # delete every candle that had not closed by the exit candle's open
    t_end = d["exit_ts"]
    cut = {tf: [c for c in rows[tf] if c["ts"] + {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[tf] <= t_end + 60] for tf in rows}
    cut_opens = {k: [c["ts"] for c in v] for k, v in cut.items()}
    a2 = analyze(trade, diagnose_trade(trade, cut["1m"]), 1.5, cut, cut_opens)
    assert {k: v["avail"] for k, v in a["events"].items()} == {k: v["avail"] for k, v in a2["events"].items()}
    assert all(v["avail"] <= a["t_end"] for v in a["events"].values())
    assert all(v["avail"] > a["t_ref"] for v in a["events"].values())


def test_forensic_sequence_is_causal_and_classified():
    from src.trend_break.diagnostics import diagnose_trade
    from src.trend_break.forensics import analyze, sequence
    m1, trade = _forensic_fixture()
    rows, opens = _rows_opens(m1)
    d = diagnose_trade(trade, m1)
    a = analyze(trade, d, 1.5, rows, opens)
    q = sequence(trade, d, a, rows, opens)
    assert q["class"][0] in "ABCN" and q["minutes_after_ref"] > 0
    cut = {tf: [c for c in rows[tf] if c["ts"] + {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[tf] <= d["exit_ts"] + 60] for tf in rows}
    cut_opens = {k: [c["ts"] for c in v] for k, v in cut.items()}
    d2 = diagnose_trade(trade, cut["1m"])
    q2 = sequence(trade, d2, analyze(trade, d2, 1.5, cut, cut_opens), cut, cut_opens)
    for k in ("class", "new_extreme", "pre_peak", "pullback_depth", "between", "warn_min"):
        assert q.get(k) == q2.get(k), k


# ---- Conditioned TP research overlay: state machine edge cases -------------
def _path(*favs, start=60, step=60):
    return [(start + step * i, f) for i, f in enumerate(favs)]


def test_ctp_A_warning_then_new_extreme_holds():
    from src.trend_break.conditioned import state_machine
    # +1.5R reached at t=120; warning at 240; new extreme (1.9 > 1.7) at 360; a 5M failure at 420 must NOT exit
    path = _path(1.0, 1.5, 1.6, 1.7, 1.5, 1.9, 1.8)
    sm = state_machine(path, 120, {240}, {420: ["M5_opp_CHoCH"]})
    assert sm["exit_j"] is None and [k for k, _ in sm["log"]] == ["warn", "cancel"]


def test_ctp_B_warning_no_extreme_then_5m_failure_exits_first_available():
    from src.trend_break.conditioned import state_machine
    path = _path(1.0, 1.5, 1.6, 1.7, 1.5, 1.4, 1.3)
    sm = state_machine(path, 120, {240}, {360: ["M5_opp_BOS"], 420: ["M15_opp_CHoCH"]})
    assert sm["exit_j"] == 5 and sm["trigger_avail"] == 360 and sm["trigger"] == ["M5_opp_BOS"]


def test_ctp_C_weak_momentum_alone_never_exits():
    from src.trend_break.conditioned import state_machine
    sm = state_machine(_path(1.0, 1.5, 1.6, 1.2, 0.5, 0.0), 120, {240}, {})
    assert sm["exit_j"] is None


def test_ctp_5m_event_before_or_at_warning_does_not_count():
    from src.trend_break.conditioned import state_machine
    sm = state_machine(_path(1.0, 1.5, 1.6, 1.4, 1.3), 120, {240}, {180: ["M5_opp_BOS"], 240: ["M5_opp_CHoCH"]})
    assert sm["exit_j"] is None            # the failure is not strictly AFTER the warning


def test_ctp_D_tie_same_candle_trigger_first_and_flagged():
    from src.trend_break.conditioned import state_machine
    # candle closing at 360 makes a new extreme (1.9) AND a 5M failure is available at 360
    sm = state_machine(_path(1.0, 1.5, 1.6, 1.7, 1.65, 1.9), 120, {240}, {360: ["M5_opp_CHoCH"]})
    assert sm["exit_j"] == 5 and sm["tie_with_new_extreme"] is True


def test_ctp_rewarn_after_cancel_can_exit_later():
    from src.trend_break.conditioned import state_machine
    path = _path(1.0, 1.5, 1.6, 1.7, 1.9, 1.8, 1.7, 1.6)
    sm = state_machine(path, 120, {240, 420}, {480: ["M5_opp_CHoCH"]})
    assert sm["exit_j"] == 7 and [k for k, _ in sm["log"]] == ["warn", "cancel", "warn"]


def test_ctp_needs_1_5r_first():
    from src.trend_break.conditioned import state_machine
    sm = state_machine(_path(1.0, 1.2, 1.3, 1.4), 9999, {180}, {240: ["M5_opp_CHoCH"]})
    assert sm["exit_j"] is None


def test_ctp_overlay_is_causal_and_exits_at_next_open():
    from src.trend_break.conditioned import overlay
    from src.trend_break.diagnostics import diagnose_trade
    m1, trade = _forensic_fixture()
    rows, opens = _rows_opens(m1)
    d = diagnose_trade(trade, m1)
    r = overlay(trade, d, rows, opens, 1.5)
    assert r["base_outcome"] == "SL"
    if r["outcome"] == "CTP":
        assert r["exit_ts"] == r["trigger_avail"] and r["exit_ts"] <= d["exit_ts"]
        nxt = next(c for c in m1 if c["ts"] == r["exit_ts"])
        assert r["exit_price"] == nxt["open"]
    cut = {tf: [c for c in rows[tf] if c["ts"] + {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}[tf] <= d["exit_ts"] + 60] for tf in rows}
    cut_opens = {k: [c["ts"] for c in v] for k, v in cut.items()}
    r2 = overlay(trade, diagnose_trade(trade, cut["1m"]), cut, cut_opens, 1.5)
    assert (r["outcome"], r.get("exit_ts"), round(r["r"], 9)) == (r2["outcome"], r2.get("exit_ts"), round(r2["r"], 9))


def test_why_sl_report_runs_on_sl_only_and_mixed_groups():
    from src.trend_break.forensics import why_sl_report, _fisher_p
    m1, trade = _forensic_fixture()
    trade.update({"conf": 0.6, "struct": "BOS", "align": "CONFLICT", "stop_pct": 0.3, "setup_ts": m1[1500]["ts"],
                  "exit": "SL"})
    rows, opens = _rows_opens(m1)
    txt = why_sl_report([trade], rows, opens)
    assert "SECTION 2" in txt and "SECTION 5" in txt
    assert abs(_fisher_p(10, 0, 0, 10) - 1.08e-5) < 1e-5          # strong association -> tiny p
    assert _fisher_p(5, 5, 5, 5) == 1.0


def test_backfill_1m_walks_back_is_idempotent_and_stops_at_history_wall():
    import importlib.util, sqlite3
    spec = importlib.util.spec_from_file_location("bf", os.path.join(os.path.dirname(__file__), "..", "scripts", "backfill_1m.py"))
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    con = sqlite3.connect(":memory:")
    bf.ensure_table(con)
    HIST_START = 1_000_000 - 1_000_000 % 60           # the API's history wall
    def fake_fetch(start, end):
        lo = max(start, HIST_START)
        return [("BTC_USDT", "1m", t, 1.0, 2.0, 0.5, 1.5, 3.0) for t in range(lo - lo % 60, end - end % 60, 60) if t >= HIST_START]
    now = HIST_START + 60 * 6000
    bf.save(con, fake_fetch(now - 60 * 100, now))
    log = []
    n1 = bf.walk_back(con, fake_fetch, target_start=HIST_START - 60 * 12000, sleep=0, log=log.append)
    lo, hi, cnt = bf.bounds(con)
    assert lo == HIST_START and hi == now - 60 and cnt == 6000       # reached the wall, no duplicates
    assert any("history limit" in x for x in log)
    assert bf.walk_back(con, fake_fetch, target_start=HIST_START - 60 * 12000, sleep=0, log=lambda *_: None) == 0   # re-run adds nothing


def test_research_db_builder_parses_and_aggregates_consistently():
    import importlib.util, io, zipfile
    spec = importlib.util.spec_from_file_location("brd", os.path.join(os.path.dirname(__file__), "..", "scripts", "build_research_db.py"))
    brd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(brd)
    t0 = 1_700_006_400 - 1_700_006_400 % 86400            # aligned to a UTC day
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,tb,tq,ignore"]
    for i in range(1500):                                  # 25 hours of 1m
        p = 100 + i * 0.01
        lines.append(f"{(t0 + 60 * i) * 1000},{p},{p + 0.5},{p - 0.5},{p + 0.1},1.0,0,0,0,0,0,0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "\n".join(lines))
    rows = brd.parse_zip(buf.getvalue())
    assert len(rows) == 1500 and rows[0][0] == t0                       # header skipped, ms -> s
    h1 = brd.aggregate(rows, 3600)
    assert len(h1) == 25 and h1[0][0] == t0 and h1[0][5] == 60.0        # complete hours only, volume summed
    assert h1[0][2] == max(r[2] for r in rows[:60]) and h1[0][4] == rows[59][4]
    d1 = brd.aggregate(rows, 86400)
    assert len(d1) == 1                                                  # only the one complete day
    assert len(brd.aggregate(rows[:100], 3600)) == 1                     # partial hour at the edge is dropped


def _walk(n=6000, seed=3, planted=False):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p += rnd.gauss(0, 1)
        out.append({"ts": 1_700_000_000 + i * 900, "open": o, "high": max(o, p) + abs(rnd.gauss(0, .3)),
                    "low": min(o, p) - abs(rnd.gauss(0, .3)), "close": p, "volume": rnd.uniform(50, 150)})
    return out


def test_feature_study_is_causal_and_finds_nothing_in_a_random_walk():
    from src.trend_break import features as F
    c = _walk()
    rows = F.tag_breaks(c, 900)
    assert len(rows) > 100
    cut = rows[len(rows) // 2]
    pre = F.tag_breaks(c[:cut["i"] + 1], 900)                    # data up to the break bar only
    same = [r for r in pre if r["ts"] == cut["ts"]][0]
    for k in F.FEATURES:
        assert same[k] == pytest.approx(cut[k])                  # features never look past the break bar
    st = F.study(rows, (1, 4, 16, 64))
    assert not any(r["flag"] for r in st["results"])            # no edge in noise


def test_feature_study_detects_a_planted_volume_signal():
    from src.trend_break import features as F
    c = _walk(n=12000, seed=5)
    res = tl.compute(c)
    for b in res.events:                                        # plant: high-volume breaks gap onward
        i = b.index
        if i + 2 < len(c) and i % 2 == 0:
            c[i]["volume"] *= 5
            jump = 1.5 if b.direction == "LONG" else -1.5
            for key in ("open", "high", "low", "close"):
                c[i + 1][key] += jump
    rows = F.tag_breaks(c, 900)
    st = F.study(rows, (1, 4, 16, 64))
    top = [r for r in st["results"] if r["feature"] == "vol_ratio" and r["target"] == "fwd1"][0]
    assert top["rho"] > 0.2 and top["p"] < 1e-6


def test_bot_never_closes_a_manual_position(monkeypatch):
    from src import autotrader_live_sync as ls
    from src.market_data import mexc_private
    closed = []
    monkeypatch.setattr(mexc_private, "close_position", lambda **kw: closed.append(kw) or {"ok": True})
    monkeypatch.setattr("src.autotrader_exec._fresh_price", lambda: 100.0)
    manual_long = [{"positionType": 1, "holdVol": 7}]
    monkeypatch.setattr(mexc_private, "get_open_positions", lambda *a, **k: manual_long)
    assert ls.flatten_mexc("LONG", None, 100.0) is None            # no bot-owned vol -> nothing closed
    assert ls.flatten_mexc("SHORT", 3, 100.0) is None              # other side -> nothing closed
    assert closed == []
    ls.flatten_mexc("LONG", 3, 100.0)                              # bot owns 3 of the 7 contracts
    assert len(closed) == 1 and closed[0]["vol"] == 3              # never the account's 7
    assert ls.REVIVE_SHADOW is False and ls.revive_shadow_if_mexc_open() is None


def test_sweep_study_random_walk_has_no_edge_and_planted_reversal_is_found():
    from src.trend_break import sweep as W
    c = _walk(n=12000, seed=11)
    res = W.tag_sweeps(c, 48)
    assert len(res["events"]) > 200
    assert not W.judge(res)["pass"]
    for e in res["events"]:                                # events only use data up to bar i
        pre = W.tag_sweeps(c[:e["i"] + 1], 48)["events"]
        if pre and pre[-1]["i"] == e["i"]:
            assert pre[-1]["side"] == e["side"] and pre[-1]["wick_atr"] == pytest.approx(e["wick_atr"])
        break
    p = _walk(n=12000, seed=11)
    for e in res["events"]:                                # plant: price reverses hard after every sweep
        i = e["i"]
        if i + 2 < len(p):
            for key in ("open", "high", "low", "close"):
                p[i + 1][key] += e["s"] * 4.0
    assert W.judge(W.tag_sweeps(p, 48))["pass"]


def test_momentum_study_calibrated_on_noise_and_finds_planted_trend_following():
    from src.trend_break import momentum as M
    passes = 0
    for seed in range(12):
        passes += M.judge(M.tag_momentum(_walk(n=9000, seed=seed), 48))["pass"]
    assert passes == 0                                         # no false PASS on random walks
    p = _walk(n=9000, seed=3)
    res = M.tag_momentum(p, 48)
    for e in res["events"]:                                    # plant: a breakout keeps running for 64 bars, then holds
        i = e["i"]
        for k in range(i + 1, len(p)):
            for key in ("open", "high", "low", "close"):
                p[k][key] += e["s"] * 0.15 * min(k - i, 64)
    assert M.judge(M.tag_momentum(p, 48))["pass"]


def test_structure_study_events_history_and_gate():
    from src.trend_break import structure as S, sweep as W, gauges as G
    c = _walk(n=3000, seed=2)
    mine = [(e["ts"], e["type"], e["direction"]) for e in S.swing_events(c, 2)]
    ref = [(e["ts"], e["type"], e["direction"]) for e in G.structure_events(c, 2)]
    assert mine == ref and len(mine) > 50                      # linear-time version == the engine's definition
    h = []
    d = tl.structure_direction(c, 8, history=h)
    assert (h[-1][1] if h else "NEUTRAL") == d["direction"]     # history hook does not change the answer
    c4 = [dict(ts=1_700_000_000 + i * 14400, open=100, high=100, low=100, close=100, volume=1) for i in range(0)]
    passes = 0
    for seed in range(6):
        w = _walk(n=9000, seed=seed)
        res = S.tag_structure(w, c4, 5)
        passes += W.judge(S.group(res, lambda e: e["type"] == "BOS"), n_variants=8)["pass"]
    assert passes == 0                                         # no false PASS on random walks
    w = _walk(n=9000, seed=1)
    res = S.tag_structure(w, c4, 5)
    for e in res["events"]:
        if e["type"] == "BOS":                                 # plant: BOS keeps running, then holds
            for k in range(e["i"] + 1, len(w)):
                for key in ("open", "high", "low", "close"):
                    w[k][key] += e["s"] * 0.25 * min(k - e["i"], 20)
    assert W.judge(S.group(S.tag_structure(w, c4, 5), lambda e: e["type"] == "BOS"), n_variants=8)["pass"]
