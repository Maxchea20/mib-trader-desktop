"""Failed 5-minute retest: same signal, fade or with the original break."""

from __future__ import annotations

from datetime import date

from src.orb.market import Bar
from src.orb.session import entry_cutoff_ts, range_bounds_ts
from src.orb.retest_fail import find_retest_failure, run_retest_fail_backtest
from src.orb.v3 import find_breakout, opening_range_v3, v3_primary_config


SUMMER = date(2026, 7, 15)


def _cfg():
    return v3_primary_config(slippage_bps=0.0, spread_usd=0.0, slippage_profile="zero")


def _book(fail_close: float, *, later=None):
    start, end = range_bounds_ts(SUMMER, 15)
    bars_15 = [Bar(start, 100.0, 101.0, 99.0, 100.0, 1.0)]
    bars_5 = [
        Bar(start, 100.0, 101.0, 99.0, 100.0, 1.0),
        Bar(start + 300, 100.0, 101.0, 99.0, 100.0, 1.0),
        Bar(start + 600, 100.0, 101.0, 99.0, 100.0, 1.0),
        Bar(end, 100.6, 104.0, 100.4, 103.0, 1.0),
        Bar(end + 300, 103.0, 103.2, 100.2, fail_close, 1.0),
    ]
    if later:
        bars_5.append(Bar(end + 600, *later, 1.0))
    fill_ts = end + 600
    bars_1 = [
        Bar(fill_ts, fail_close, fail_close + 0.2, fail_close - 0.2, fail_close, 1.0),
        Bar(fill_ts + 60, fail_close, fail_close + 0.2, 90.0, 91.0, 1.0),
    ]
    return bars_15, bars_5, bars_1


def test_a_close_back_through_the_level_is_the_failure_and_the_direction_is_the_only_change():
    bars_15, bars_5, bars_1 = _book(100.5)
    rng = opening_range_v3(bars_15, SUMMER, bars_5m=bars_5)
    brk = find_breakout(bars_5, rng, cutoff_ts=entry_cutoff_ts(SUMMER))
    failure = find_retest_failure(bars_5, rng, brk, cutoff_ts=entry_cutoff_ts(SUMMER))
    assert failure.close == 100.5
    assert failure.close < rng.high

    fade = [row for row in run_retest_fail_backtest(bars_15, bars_5, bars_1, _cfg(), direction="fade") if row["status"] == "closed"][0]
    held = [row for row in run_retest_fail_backtest(bars_15, bars_5, bars_1, _cfg(), direction="with") if row["status"] == "closed"][0]
    assert fade["breakout_side"] == "LONG"
    assert fade["direction"] == "SHORT"
    assert held["direction"] == "LONG"
    assert fade["fill_ts"] == held["fill_ts"] == fade["entry_signal_ts"]
    assert fade["raw_entry"] == 100.5
    assert fade["fill_price"] == 100.5
    assert fade["exit_reason"] == "take_profit"
    assert held["exit_reason"] == "stop"


def test_a_wick_back_to_the_level_is_not_a_failure():
    start, end = range_bounds_ts(SUMMER, 15)
    bars_15, bars_5, _bars_1 = _book(102.0, later=(101.5, 101.6, 100.0, 100.4))
    signal_ts = end + 900
    bars_1 = [
        Bar(signal_ts, 100.4, 100.5, 100.2, 100.3, 1.0),
        Bar(signal_ts + 60, 100.3, 100.4, 90.0, 91.0, 1.0),
    ]
    rows = run_retest_fail_backtest(bars_15, bars_5, bars_1, _cfg(), direction="fade")
    assert rows[0]["status"] == "closed"
    assert rows[0]["failure_close"] == 100.4
    assert rows[0]["entry_signal_ts"] == signal_ts
    assert rows[0]["direction"] == "SHORT"
    assert start < end


def test_no_close_back_through_means_no_trade():
    start, end = range_bounds_ts(SUMMER, 15)
    bars_15, bars_5, _bars_1 = _book(102.0)
    cutoff = entry_cutoff_ts(SUMMER)
    ts = end + 600
    while ts + 300 < cutoff:
        bars_5.append(Bar(ts, 102.0, 102.4, 101.4, 102.2, 1.0))
        ts += 300
    rows = run_retest_fail_backtest(bars_15, bars_5, [], _cfg(), direction="fade")
    assert rows[0]["status"] == "unfilled"
    assert rows[0]["unfilled_reason"] == "no_retest_failure"
    assert start < cutoff
