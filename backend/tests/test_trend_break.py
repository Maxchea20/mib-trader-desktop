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
        assert e.index >= e.line.confirm_index >= e.line.pivot_index + 14


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
