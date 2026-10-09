"""ORB V3: 09:30-09:45 15-minute range, 5-minute body breakout, 1-minute continuation.

Synthetic candles only. No network and no live orders.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.orb.market import Bar, execution_price, size_contracts, stop_and_target
from src.orb.session import entry_cutoff_ts, range_bounds_ts
from src.orb.v3 import find_breakout, opening_range_v3, run_v3_backtest, v3_primary_config


NY = ZoneInfo("America/New_York")
WINTER = date(2026, 1, 15)  # Thursday, EST
SUMMER = date(2026, 7, 15)  # Wednesday, EDT


def _cfg(**kwargs):
    base = dict(slippage_bps=0.0, spread_usd=0.0, slippage_profile="zero")
    base.update(kwargs)
    return v3_primary_config(**base)


def _interior(start, o, h, l, c):
    return [
        Bar(start, o, h, l, o, 1.0),
        Bar(start + 300, o, o, o, o, 1.0),
        Bar(start + 600, o, max(o, c), min(o, c), c, 1.0),
    ]


def _session(day, later_fives, later_ones, or_ohlc=(100.0, 101.0, 99.0, 100.0), *, interior=None):
    start, end = range_bounds_ts(day, 15)
    o, h, l, c = or_ohlc
    bars_15 = [Bar(start, o, h, l, c, 1.0), Bar(end, o, h + 50.0, l - 50.0, o, 1.0)]
    parts = interior if interior is not None else _interior(start, o, h, l, c)
    bars_5 = list(parts) + [Bar(end + i * 300, *ohlc, 1.0) for i, ohlc in enumerate(later_fives)]
    bars_1 = [Bar(start + minute * 60, *ohlc, 1.0) for minute, ohlc in later_ones]
    return bars_15, bars_5, bars_1


def _closed(rows):
    return [row for row in rows if row["status"] == "closed"]


def _filled(rows):
    return [row for row in rows if row["status"] in ("closed", "filled_open")]


def test_opening_range_is_the_0930_to_0945_fifteen_minute_candle():
    for day, utc_hour in (
        (WINTER, 14),
        (SUMMER, 13),
        (date(2026, 3, 6), 14),   # Friday before the US spring change
        (date(2026, 3, 9), 13),   # Monday after the spring change
        (date(2026, 10, 30), 13), # Friday before the autumn change
        (date(2026, 11, 2), 14),  # Monday after the autumn change
    ):
        start, end = range_bounds_ts(day, 15)
        opened = datetime.fromtimestamp(start, timezone.utc)
        closed = datetime.fromtimestamp(end, timezone.utc)
        assert opened.hour == utc_hour and opened.minute == 30
        assert closed.hour == utc_hour and closed.minute == 45
        assert end - start == 900
        local_open = datetime.fromtimestamp(start, timezone.utc).astimezone(NY)
        local_close = datetime.fromtimestamp(end, timezone.utc).astimezone(NY)
        assert (local_open.hour, local_open.minute) == (9, 30)
        assert (local_close.hour, local_close.minute) == (9, 45)
        # The first 5-minute candle is a different, tighter range. It must not win.
        first_5m = Bar(start, 100, 102, 98, 100, 1)
        mid = Bar(start + 300, 100, 130, 100, 100, 1)
        last = Bar(start + 600, 100, 100, 70, 100, 1)
        candle = Bar(start, 100, 130, 70, 100, 1)
        later = Bar(end, 100, 400, 10, 100, 1)
        rng = opening_range_v3([candle, later], day, bars_5m=[first_5m, mid, last])
        assert rng.status == "ok"
        assert rng.high == 130 and rng.low == 70
        assert rng.high != first_5m.high and rng.low != first_5m.low
        assert rng.start_ts == start and rng.end_ts == end
        assert opening_range_v3([candle, later], day, bars_5m=[first_5m, mid, last]).high == 130


def test_range_is_frozen_and_rejects_a_bad_or_missing_fifteen_minute_candle():
    start, end = range_bounds_ts(SUMMER, 15)
    candle = Bar(start, 100, 110, 90, 105, 1)
    parts = _interior(start, 100, 110, 90, 105)
    widened = Bar(end, 105, 250, 40, 140, 1)
    rng = opening_range_v3([candle, widened], SUMMER, bars_5m=parts)
    assert rng.high == 110 and rng.low == 90
    assert rng.end_ts == end
    assert opening_range_v3([widened], SUMMER, bars_5m=parts).status == "missing_opening_range"
    holiday = date(2026, 9, 7)
    holiday_start, _ = range_bounds_ts(holiday, 15)
    holiday_candle = Bar(holiday_start, 100, 110, 90, 100, 1)
    assert opening_range_v3([holiday_candle], holiday).status == "not_nyse_session_day"
    assert _filled(run_v3_backtest([holiday_candle], parts, [], _cfg())) == []
    # Three 5-minute candles that do not rebuild the 15-minute OHLC skip the day.
    mismatched = [
        Bar(start, 100, 110, 90, 100, 1),
        Bar(start + 300, 100, 110, 90, 100, 1),
        Bar(start + 600, 100, 110, 90, 101, 1),
    ]
    assert opening_range_v3([candle], SUMMER, bars_5m=mismatched).status == "opening_range_mismatch"


def test_breakout_search_starts_after_0945_and_accepts_a_later_five_minute_candle():
    start, end = range_bounds_ts(SUMMER, 15)
    cutoff = entry_cutoff_ts(SUMMER)
    candle = Bar(start, 100, 110, 90, 100, 1)
    matching = _interior(start, 100, 110, 90, 100)
    rng = opening_range_v3([candle], SUMMER, bars_5m=matching)
    assert rng.usable and rng.end_ts == end

    def path(break_ts=None):
        bars = []
        ts = end
        while ts + 300 < cutoff:
            if break_ts is not None and ts == break_ts:
                bars.append(Bar(ts, 109, 116, 108, 115, 1))
            else:
                bars.append(Bar(ts, 100, 109, 99, 108, 1))
            ts += 300
        return bars

    # 09:40 closes far above the 15-minute high. It is still inside the range
    # window, so it is not a breakout. A complete later path that stays inside
    # produces no trade direction.
    early = Bar(start + 600, 100, 180, 90, 170, 1)
    ignored = find_breakout(matching[:2] + [early] + path(), rng, cutoff_ts=cutoff)
    assert ignored is None
    found = find_breakout(matching + path(end + 600), rng, cutoff_ts=cutoff)
    assert found is not None and not isinstance(found, str)
    assert found.candle_ts == end + 600
    assert found.candle_ts >= rng.end_ts
    assert found.close == 115 and found.side == "LONG"
    # A hole at 09:45 cannot be repaired by the 09:55 break.
    gapped = matching + [bar for bar in path(end + 600) if bar.ts != end]
    assert find_breakout(gapped, rng, cutoff_ts=cutoff) == "incomplete"


def test_wick_only_break_is_rejected_and_direction_rules_hold():
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.4, 120.0, 99.5, 100.8)],
        [(18, (100.4, 120.0, 100.4, 104.0))],
    )
    rows = run_v3_backtest(bars_15, bars_5, bars_1, _cfg())
    assert _filled(rows) == []
    assert [row for row in rows if row.get("entry_signal_ts")] == []

    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.2, 100.6, 96.0, 97.0)],
        [
            (20, (98.0, 98.2, 96.4, 96.8)),
            (21, (96.6, 96.8, 96.2, 96.4)),
        ],
    )
    trade = _filled(run_v3_backtest(bars_15, bars_5, bars_1, _cfg()))[0]
    assert trade["direction"] == "SHORT"
    assert trade["or_high"] == 101.0 and trade["or_low"] == 99.0
    assert trade["breakout_close"] == 97.0
    assert trade["breakout_candle_ts"] == range_bounds_ts(SUMMER, 15)[1]


def test_v2_five_minute_high_is_not_the_opening_range():
    # 15-minute high is 110. The 09:30 five-minute high is only 101.
    # A later close of 105 would have been a V2 long. It is not a V3 long.
    start, end = range_bounds_ts(SUMMER, 15)
    bars_15 = [Bar(start, 100, 110, 90, 100, 1)]
    bars_5 = [
        Bar(start, 100, 101, 99, 100, 1),
        Bar(start + 300, 100, 110, 100, 100, 1),
        Bar(start + 600, 100, 100, 90, 100, 1),
        Bar(end, 100, 106, 100, 105, 1),          # above 101, not above 110
        Bar(end + 300, 105, 114, 104, 112, 1),    # first real break
    ]
    bars_1 = [
        Bar(start + 20 * 60, 104, 106, 103, 105.5, 1),  # would qualify against 101
        Bar(start + 21 * 60, 105.5, 106, 105, 105.8, 1),
        Bar(start + 25 * 60, 111, 113, 110.5, 112.4, 1),
        Bar(start + 26 * 60, 112.4, 112.8, 112.0, 112.6, 1),
    ]
    trade = _filled(run_v3_backtest(bars_15, bars_5, bars_1, _cfg()))[0]
    assert trade["or_high"] == 110.0
    assert trade["or_low"] == 90.0
    assert trade["breakout_close"] == 112.0
    assert trade["breakout_open"] == 105.0
    assert trade["breakout_high"] == 114.0
    assert trade["breakout_low"] == 104.0
    assert trade["direction"] == "LONG"
    assert trade["entry_signal_close"] == 112.4
    assert trade["raw_entry"] == 112.4
    assert trade["entry_signal_ts"] > trade["breakout_close_ts"]


def test_one_minute_signal_cannot_precede_breakout_confirmation():
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (18, (101.0, 106.0, 101.0, 105.0)),  # inside the 09:45-09:50 breakout bar
            (20, (103.0, 103.1, 102.9, 103.0)),  # doji after confirmation
        ],
    )
    assert _filled(run_v3_backtest(bars_15, bars_5, bars_1, _cfg())) == []

    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (18, (101.0, 106.0, 101.0, 105.0)),
            (20, (102.2, 103.0, 102.0, 102.7)),
            (21, (102.9, 103.2, 102.6, 103.0)),
        ],
    )
    trade = _filled(run_v3_backtest(bars_15, bars_5, bars_1, _cfg()))[0]
    assert trade["entry_signal_ts"] > trade["breakout_close_ts"]
    assert trade["fill_ts"] == trade["info_known_ts"]
    assert trade["fill_ts"] >= trade["info_known_ts"]
    assert trade["raw_entry"] == 102.9
    assert trade["entry_signal_close"] == 102.7
    assert trade["raw_entry"] != trade["entry_signal_close"]
    assert trade["range_end_ts"] - trade["range_start_ts"] == 900


def test_eleven_oclock_cutoff_cancels_the_entry_and_keeps_an_open_position():
    cutoff = entry_cutoff_ts(SUMMER)
    # Signal closes at 10:59. The next stored bar is 11:00, so there is no fill.
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (88, (101.2, 103.0, 101.1, 102.5)),  # 10:58-10:59
            (90, (110.0, 130.0, 109.0, 125.0)),  # 11:00
        ],
    )
    rows = run_v3_backtest(bars_15, bars_5, bars_1, _cfg())
    assert len(rows) == 1
    assert rows[0]["status"] == "unfilled"
    assert rows[0]["unfilled_reason"] == "entry_cutoff"
    assert rows[0]["entry_signal_ts"] < cutoff
    assert _filled(rows) == []

    # Fill at 10:58, still flat at 10:59, target on the 11:00 bar. Not flattened at the cutoff.
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (87, (102.0, 103.0, 101.8, 102.6)),  # signal 10:57-10:58
            (88, (102.7, 102.9, 102.5, 102.8)),  # fill 10:58
            (89, (102.8, 103.0, 102.6, 102.9)),  # 10:59, no exit
            (90, (102.9, 110.0, 102.8, 109.0)),  # 11:00 target
        ],
    )
    trade = _closed(run_v3_backtest(bars_15, bars_5, bars_1, _cfg()))[0]
    assert trade["fill_ts"] < cutoff
    assert trade["exit_ts"] >= cutoff
    assert trade["exit_reason"] == "take_profit"
    assert trade["fill_ts"] != cutoff


def test_direction_does_not_reverse_and_a_stopped_trade_is_not_reentered():
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [
            (100.6, 103.0, 100.4, 102.5),
            (100.0, 100.4, 90.0, 95.0),
        ],
        [
            (20, (100.2, 100.4, 94.0, 96.0)),
            (21, (96.0, 96.2, 94.5, 95.0)),
            (25, (96.0, 96.1, 93.0, 94.0)),
            (26, (94.0, 94.2, 93.5, 93.8)),
        ],
    )
    assert _filled(run_v3_backtest(bars_15, bars_5, bars_1, _cfg())) == []

    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (20, (102.0, 103.0, 101.6, 102.6)),
            (21, (102.6, 102.8, 90.0, 95.0)),  # fill and stop
            (22, (101.0, 106.0, 100.8, 105.0)),
            (23, (105.0, 106.0, 104.0, 105.5)),
        ],
    )
    rows = run_v3_backtest(bars_15, bars_5, bars_1, _cfg())
    filled = _filled(rows)
    assert len(filled) == 1
    assert filled[0]["status"] == "closed"
    assert filled[0]["exit_reason"] == "stop"
    assert filled[0]["direction"] == "LONG"
    assert sum(1 for row in rows if row.get("entry_signal_ts")) == 1


def test_no_entry_without_a_qualifying_one_minute_continuation():
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (20, (102.0, 102.4, 100.0, 100.4)),  # bullish, but not above 101
            (21, (100.4, 100.8, 100.2, 100.6)),
        ],
    )
    rows = run_v3_backtest(bars_15, bars_5, bars_1, _cfg())
    assert _filled(rows) == []
    assert rows[0]["unfilled_reason"] == "no_1m_continuation"
    assert rows[0]["direction"] == "LONG"
    assert rows[0]["breakout_close"] == 103.0
    assert rows[0]["or_high"] == 101.0


def test_fill_fees_stop_and_target_match_the_shared_formulas():
    cfg = v3_primary_config()
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100100.0, 100400.0, 100050.0, 100300.0)],
        [
            (20, (100200.0, 100350.0, 100180.0, 100280.0)),
            (21, (100320.0, 100360.0, 100300.0, 100340.0)),
            (22, (100300.0, 100320.0, 99000.0, 99500.0)),
        ],
        or_ohlc=(100000.0, 100100.0, 99900.0, 100000.0),
    )
    trade = _closed(run_v3_backtest(bars_15, bars_5, bars_1, cfg))[0]
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
    assert trade["r_multiple"] == pytest.approx(net / (abs(fill - sl) * qty * 0.0001))
    assert trade["exit_reason"] == "stop"
    assert trade["path_ambiguous"] is False
    assert trade["ohlc_proxy"] is True
    assert trade["simulated_fill"] is True
    assert trade["leverage"] == 5.0
    assert trade["fill_price"] > trade["raw_entry"]
    assert v3_primary_config().to_dict()["live_submit"] is False


def test_same_bar_stop_and_target_take_the_stop():
    bars_15, bars_5, bars_1 = _session(
        SUMMER,
        [(100.6, 104.0, 100.4, 103.0)],
        [
            (20, (102.0, 103.0, 101.8, 102.6)),
            (21, (102.7, 120.0, 80.0, 110.0)),
        ],
    )
    trade = _closed(run_v3_backtest(bars_15, bars_5, bars_1, _cfg()))[0]
    assert trade["exit_reason"] == "stop"
    assert trade["path_ambiguous"] is True
    assert trade["same_bar_exit"] is True


def test_a_five_minute_candle_that_closes_at_1100_is_not_a_breakout():
    start, end = range_bounds_ts(SUMMER, 15)
    cutoff = entry_cutoff_ts(SUMMER)
    candle = Bar(start, 100, 101, 99, 100, 1)
    matching = _interior(start, 100, 101, 99, 100)
    rng = opening_range_v3([candle], SUMMER, bars_5m=matching)
    slots = []
    ts = end
    while ts + 300 < cutoff:
        slots.append(Bar(ts, 100, 100.5, 99.5, 100.2, 1))
        ts += 300
    assert ts + 300 == cutoff
    at_cutoff = Bar(ts, 100, 130, 100, 120, 1)
    assert find_breakout(matching + slots + [at_cutoff], rng, cutoff_ts=cutoff) is None
    # The last legal 5-minute slot, 10:50-10:55, can still break.
    slots[-1] = Bar(slots[-1].ts, 100.4, 108, 100.2, 107, 1)
    found = find_breakout(matching + slots + [at_cutoff], rng, cutoff_ts=cutoff)
    assert not isinstance(found, str) and found is not None
    assert found.close_ts < cutoff
    assert found.close == 107
