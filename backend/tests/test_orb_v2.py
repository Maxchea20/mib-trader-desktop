"""ORB V2: 09:30 range, 5-minute body breakout, 1-minute continuation.

Synthetic candles only. No network and no live orders.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.orb.market import Bar, execution_price, size_contracts, stop_and_target
from src.orb.session import entry_cutoff_ts, range_bounds_ts
from src.orb.v2 import opening_range_v2, run_v2_backtest, v2_primary_config


WINTER = date(2026, 1, 15)  # Thursday, EST
SUMMER = date(2026, 7, 15)  # Wednesday, EDT


def _cfg(**kwargs):
    base = dict(slippage_bps=0.0, spread_usd=0.0, slippage_profile="zero")
    base.update(kwargs)
    return v2_primary_config(**base)


def _range_minutes(o, h, l, c):
    return [
        (o, h, l, o),
        (o, o, o, o),
        (o, o, o, o),
        (o, o, o, o),
        (o, max(o, c), min(o, c), c),
    ]


def _bars(day, fives, ones):
    start, _ = range_bounds_ts(day, 5)
    bars_5m = [Bar(start + i * 300, *ohlc, 1.0) for i, ohlc in enumerate(fives)]
    bars_1m = [Bar(start + minute * 60, *ohlc, 1.0) for minute, ohlc in ones]
    return bars_5m, bars_1m


def _with_range(day, later_fives, later_ones, or_ohlc=(100.0, 101.0, 99.0, 100.0)):
    fives = [or_ohlc, *later_fives]
    ones = list(enumerate(_range_minutes(*or_ohlc))) + list(later_ones)
    return _bars(day, fives, ones)


def _closed(rows):
    return [row for row in rows if row["status"] == "closed"]


def _filled(rows):
    return [row for row in rows if row["status"] in ("closed", "filled_open")]


def test_dst_selects_the_0930_new_york_candle():
    for day, utc_hour in (
        (WINTER, 14),
        (SUMMER, 13),
        (date(2026, 3, 6), 14),   # Friday before the US spring change
        (date(2026, 3, 9), 13),   # Monday after the spring change
        (date(2026, 10, 30), 13), # Friday before the autumn change
        (date(2026, 11, 2), 14),  # Monday after the autumn change
    ):
        start, end = range_bounds_ts(day, 5)
        opened = datetime.fromtimestamp(start, timezone.utc)
        assert opened.hour == utc_hour and opened.minute == 30
        assert end - start == 300
        decoy = Bar(start - 3600, 50, 999, 40, 60, 1)
        real = Bar(start, 100, 111, 90, 105, 1)
        later = Bar(start + 300, 105, 500, 80, 110, 1)
        minutes = _range_minutes(100, 111, 90, 105)
        ones = [Bar(start + i * 60, *minutes[i], 1.0) for i in range(5)]
        rng = opening_range_v2([decoy, real, later], ones, day)
        assert rng.status == "ok"
        assert rng.high == 111
        assert rng.low == 90
        assert rng.start_ts == start
        assert rng.end_ts == end
        # A later candle cannot move the range.
        assert opening_range_v2([decoy, real, later], ones, day).high == 111


def test_opening_range_is_fixed_after_0935_and_rejects_a_bad_range():
    start, _ = range_bounds_ts(SUMMER, 5)
    minutes = _range_minutes(100, 101, 99, 100)
    ones = [Bar(start + i * 60, *minutes[i], 1.0) for i in range(5)]
    first = Bar(start, 100, 101, 99, 100, 1)
    later = Bar(start + 300, 100, 250, 50, 140, 1)
    rng = opening_range_v2([first, later], ones, SUMMER)
    assert rng.high == 101 and rng.low == 99
    assert rng.end_ts == start + 300
    # Missing one of the five opening minutes skips the session.
    dropped = [bar for bar in ones if bar.ts != start + 120]
    assert opening_range_v2([first], dropped, SUMMER).status == "missing_1m_opening_range"
    # 1-minute highs that do not rebuild the 5-minute candle are not used.
    mismatched = list(ones)
    mismatched[1] = Bar(start + 60, 100, 180, 100, 100, 1)
    assert opening_range_v2([first], mismatched, SUMMER).status == "opening_range_mismatch"
    holiday_start, _ = range_bounds_ts(date(2026, 9, 7), 5)
    holiday_minutes = [Bar(holiday_start + i * 60, *minutes[i], 1.0) for i in range(5)]
    holiday_candle = Bar(holiday_start, 100, 101, 99, 100, 1)
    assert opening_range_v2([holiday_candle], holiday_minutes, date(2026, 9, 7)).status == "not_nyse_session_day"
    assert _filled(run_v2_backtest([holiday_candle], holiday_minutes, _cfg())) == []


def test_wick_only_break_is_rejected():
    # High trades through 101. The close comes back inside. Not a body break.
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.4, 120.0, 99.5, 100.8)],
        [(8, (100.4, 120.0, 100.4, 104.0))],  # tempting 1m inside a non-breakout
    )
    assert _filled(run_v2_backtest(bars_5m, bars_1m, _cfg())) == []
    assert [row for row in run_v2_backtest(bars_5m, bars_1m, _cfg()) if row.get("entry_signal_ts")] == []


def test_completed_5m_body_breakout_is_required():
    # Close finishes beyond 101. A 1-minute continuation after that close can fill.
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0), (103.0, 103.2, 102.8, 103.0)],
        [
            (10, (102.0, 103.2, 101.8, 102.8)),  # first eligible minute
            (11, (103.0, 103.4, 102.6, 103.1)),  # fill open
        ],
    )
    filled = _filled(run_v2_backtest(bars_5m, bars_1m, _cfg()))
    assert len(filled) == 1
    assert filled[0]["direction"] == "LONG"
    assert filled[0]["breakout_close"] == 103.0
    assert filled[0]["or_high"] == 101.0
    # A hole at 09:35 cannot be repaired by a later close beyond the high.
    start, _ = range_bounds_ts(SUMMER, 5)
    gapped = [bars_5m[0], Bar(start + 600, 100.0, 110.0, 100.0, 108.0, 1)]
    assert _filled(run_v2_backtest(gapped, bars_1m, _cfg())) == []


def test_1m_trigger_cannot_happen_before_breakout_confirmation():
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (8, (101.0, 106.0, 101.0, 105.0)),  # bullish and beyond, but still inside the 5m bar
            (10, (103.0, 103.1, 102.9, 103.0)),  # doji after confirmation: not a continuation
        ],
    )
    assert _filled(run_v2_backtest(bars_5m, bars_1m, _cfg())) == []

    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (8, (101.0, 106.0, 101.0, 105.0)),
            (10, (102.2, 103.0, 102.0, 102.7)),
            (11, (102.9, 103.2, 102.6, 103.0)),
        ],
    )
    trade = _filled(run_v2_backtest(bars_5m, bars_1m, _cfg()))[0]
    assert trade["entry_signal_ts"] > trade["breakout_close_ts"]
    assert trade["fill_ts"] >= trade["info_known_ts"]
    assert trade["fill_ts"] == bars_1m[-1].ts
    assert trade["raw_entry"] == 102.9
    assert trade["entry_signal_close"] == 102.7
    assert trade["raw_entry"] != trade["entry_signal_close"]


def test_1m_continuation_must_match_the_breakout_direction():
    # Long breakout. A bearish minute below the low is not a short, and a
    # bullish minute that does not close above the high is not a long.
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0), (100.0, 100.5, 90.0, 92.0)],
        [
            (10, (102.0, 102.4, 98.0, 98.5)),   # bearish, below the low
            (11, (98.5, 99.0, 97.0, 97.5)),
            (12, (100.2, 101.2, 100.0, 101.0)),  # bullish, but not above 101
            (13, (101.0, 101.2, 100.6, 100.8)),
        ],
    )
    assert _filled(run_v2_backtest(bars_5m, bars_1m, _cfg())) == []

    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.4, 100.6, 96.0, 97.0)],
        [
            (10, (96.8, 99.2, 96.5, 99.0)),  # bullish, close is not below 99
            (11, (97.2, 97.5, 97.1, 97.4)),  # bullish, still not a short
            (12, (98.0, 98.2, 96.4, 96.8)),  # bearish close below 99
            (13, (96.6, 96.8, 96.2, 96.4)),
        ],
    )
    trade = _filled(run_v2_backtest(bars_5m, bars_1m, _cfg()))[0]
    assert trade["direction"] == "SHORT"
    assert trade["breakout_close"] == 97.0
    assert trade["raw_entry"] == 96.6
    assert trade["entry_signal_close"] == 96.8


def test_no_new_entry_at_or_after_1100_and_the_position_is_not_flattened():
    cutoff = entry_cutoff_ts(SUMMER)
    # Last legal signal would need a following open before 11:00. Here the
    # next printable bar is exactly 11:00, so the order is cancelled.
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (87, (101.2, 103.0, 101.1, 102.5)),  # closes 10:58
            (90, (110.0, 130.0, 109.0, 125.0)),  # 11:00 open, not an entry
        ],
    )
    rows = run_v2_backtest(bars_5m, bars_1m, _cfg())
    assert len(rows) == 1
    assert rows[0]["status"] == "unfilled"
    assert rows[0]["unfilled_reason"] == "entry_cutoff"
    assert rows[0]["entry_signal_ts"] < cutoff
    assert _filled(rows) == []

    # A fill before 11:00 stays open through 11:00 and can still reach its target.
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (10, (102.0, 103.0, 101.8, 102.6)),
            (11, (102.7, 102.9, 102.5, 102.8)),
            (90, (102.8, 103.0, 102.6, 102.9)),   # 11:00, also a fresh bullish close
            (91, (102.9, 110.0, 102.8, 109.0)),   # target after the cutoff
        ],
    )
    trade = _closed(run_v2_backtest(bars_5m, bars_1m, _cfg()))[0]
    assert trade["fill_ts"] < cutoff
    assert trade["exit_ts"] >= cutoff
    assert trade["exit_reason"] == "take_profit"
    assert trade["fill_ts"] != cutoff


def test_only_the_first_breakout_direction_can_fill():
    # Long breakout prints first. A later close below the low does not flip
    # the day to short, even though the short minute would otherwise qualify.
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [
            (100.6, 103.0, 100.4, 102.5),
            (100.0, 100.4, 90.0, 95.0),
        ],
        [
            (10, (100.2, 100.4, 94.0, 96.0)),
            (11, (96.0, 96.2, 94.5, 95.0)),
            (15, (96.0, 96.1, 93.0, 94.0)),
            (16, (94.0, 94.2, 93.5, 93.8)),
        ],
    )
    assert _filled(run_v2_backtest(bars_5m, bars_1m, _cfg())) == []


def test_duplicate_entries_are_prevented():
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (10, (102.0, 103.0, 101.6, 102.6)),
            (11, (102.6, 102.8, 90.0, 95.0)),   # fill and stop
            (12, (101.0, 106.0, 100.8, 105.0)),  # second continuation
            (13, (105.0, 106.0, 104.0, 105.5)),
        ],
    )
    rows = run_v2_backtest(bars_5m, bars_1m, _cfg())
    filled = _filled(rows)
    assert len(filled) == 1
    assert filled[0]["status"] == "closed"
    assert filled[0]["exit_reason"] == "stop"
    assert sum(1 for row in rows if row.get("entry_signal_ts")) == 1


def test_fill_fees_stop_and_target_match_the_shared_formulas():
    cfg = v2_primary_config()
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100100.0, 100400.0, 100050.0, 100300.0)],
        [
            (10, (100200.0, 100350.0, 100180.0, 100280.0)),
            (11, (100320.0, 100360.0, 100300.0, 100340.0)),  # fill, stop not touched
            (12, (100300.0, 100320.0, 99000.0, 99500.0)),    # stop, target not touched
        ],
        or_ohlc=(100000.0, 100100.0, 99900.0, 100000.0),
    )
    trade = _closed(run_v2_backtest(bars_5m, bars_1m, cfg))[0]
    fill = execution_price(
        trade["raw_entry"], "LONG", "entry",
        slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=0.1,
    )
    sl, tp = stop_and_target("LONG", fill, cfg.stop_loss_fraction, cfg.take_profit_fraction, 0.1)
    exit_px = execution_price(
        trade["raw_exit"], "LONG", "exit",
        slippage_bps=cfg.slippage_bps, spread_usd=cfg.spread_usd, unit=0.1,
    )
    qty, err = size_contracts(cfg.starting_equity, cfg.risk_fraction, fill, sl, cfg.leverage)
    assert err is None
    fee_entry = qty * 0.0001 * fill * 0.0002
    fee_exit = abs(qty * 0.0001 * exit_px) * 0.0002
    gross = (trade["raw_exit"] - trade["raw_entry"]) * qty * 0.0001
    net = (exit_px - fill) * qty * 0.0001 - fee_entry - fee_exit
    assert trade["raw_entry"] == 100320.0
    assert trade["raw_entry"] != trade["entry_signal_close"]
    assert trade["fill_price"] == pytest.approx(fill)
    assert trade["stop_price"] == pytest.approx(sl)
    assert trade["target_price"] == pytest.approx(tp)
    assert trade["exit_price"] == pytest.approx(exit_px)
    assert trade["qty"] == qty
    assert trade["fee_entry"] == pytest.approx(fee_entry)
    assert trade["fee_exit"] == pytest.approx(fee_exit)
    assert trade["fees"] == pytest.approx(fee_entry + fee_exit)
    assert trade["gross_pnl"] == pytest.approx(gross)
    assert trade["net_pnl"] == pytest.approx(net)
    assert trade["exit_reason"] == "stop"
    assert trade["path_ambiguous"] is False
    assert trade["ohlc_proxy"] is True
    assert trade["simulated_fill"] is True
    assert trade["leverage"] == 5.0
    assert trade["fill_price"] > trade["raw_entry"]


def test_distance_filter_is_off_by_default_and_blocks_only_when_enabled():
    later_ones = [
        (10, (103.0, 104.0, 102.8, 103.6)),
        (11, (106.0, 106.4, 105.5, 106.0)),  # farther than one range width
        (12, (102.2, 102.6, 102.0, 102.4)),  # would have been a closer second entry
        (13, (102.4, 102.6, 102.2, 102.5)),
    ]
    fives = [(101.2, 104.0, 101.0, 103.4)]
    bars_5m, bars_1m = _with_range(SUMMER, fives, later_ones, or_ohlc=(100.0, 102.0, 100.0, 100.5))
    primary = _filled(run_v2_backtest(bars_5m, bars_1m, _cfg()))
    assert len(primary) == 1
    assert primary[0]["raw_entry"] == 106.0
    filtered = run_v2_backtest(
        bars_5m, bars_1m, _cfg(max_entry_distance_range_multiple=1.0),
    )
    assert _filled(filtered) == []
    assert filtered[0]["unfilled_reason"] == "entry_distance"
    assert filtered[0]["entry_signal_ts"] is not None


def test_same_bar_stop_and_target_take_the_stop():
    bars_5m, bars_1m = _with_range(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (10, (102.0, 103.0, 101.8, 102.6)),
            (11, (102.7, 120.0, 80.0, 110.0)),
        ],
    )
    trade = _closed(run_v2_backtest(bars_5m, bars_1m, _cfg()))[0]
    assert trade["exit_reason"] == "stop"
    assert trade["path_ambiguous"] is True
    assert trade["same_bar_exit"] is True
