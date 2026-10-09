"""ORB engine tests: timezone, boundaries, cutoff, lookahead, fills, risk.

Synthetic candles only. No network and no live orders.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.orb.contract_spec import BTC_USDT
from src.orb.engine import OrbConfig, chronological_splits, run_backtest
from src.orb.live import LiveSubmitDisabled, OrbLiveSession, prepare_market_order
from src.orb.market import Bar, build_opening_ranges, execution_price, resolve_exit, size_contracts
from src.orb.session import (
    entry_cutoff_ts,
    is_nyse_session_day,
    range_bounds_ts,
    session_open_utc,
    to_utc_ts,
)


DAY = date(2026, 7, 15)  # Wednesday, EDT


def _bars(day, rows, step=300, start_minute_offset=0):
    start, _ = range_bounds_ts(day, 5)
    start += start_minute_offset
    out = []
    for i, (o, h, l, c) in enumerate(rows):
        out.append(Bar(start + i * step, o, h, l, c, 1.0))
    return out


def _cfg(**kwargs) -> OrbConfig:
    base = dict(
        slippage_bps=0.0,
        spread_usd=0.0,
        slippage_profile="normal",
        timeframe="5m",
        bar_seconds=300,
    )
    base.update(kwargs)
    return OrbConfig(**base)


def test_dst_moves_the_utc_instant_of_0930():
    january = session_open_utc(date(2026, 1, 15))
    july = session_open_utc(date(2026, 7, 15))
    assert january.hour == 14 and january.minute == 30
    assert july.hour == 13 and july.minute == 30
    # 2026-03-08 and 2026-11-01 are the US transitions. 09:30 is after 02:00,
    # so the Friday before and the Monday after sit on opposite UTC hours.
    assert session_open_utc(date(2026, 3, 6)).hour == 14
    assert session_open_utc(date(2026, 3, 9)).hour == 13
    assert session_open_utc(date(2026, 10, 30)).hour == 13
    assert session_open_utc(date(2026, 11, 2)).hour == 14


def test_weekend_and_holiday_are_not_session_days():
    assert is_nyse_session_day(DAY)
    assert not is_nyse_session_day(date(2026, 7, 18))  # Saturday
    assert not is_nyse_session_day(date(2026, 9, 7))  # Labor Day
    assert not is_nyse_session_day(date(2026, 7, 3))  # Independence Day observed
    saturday = _bars(date(2026, 7, 18), [(100, 101, 99, 100), (100, 120, 100, 120), (120, 121, 119, 120)])
    ranges = build_opening_ranges(saturday, "ORB-5", 300)
    assert ranges[date(2026, 7, 18)].reason == "not_nyse_session_day"
    assert run_backtest(saturday, _cfg()) == []


def test_opening_range_excludes_candles_outside_the_window_and_rejects_gaps():
    # 1-minute bars from 09:25. The 09:29 and 09:35 candles must not count.
    start, end = range_bounds_ts(DAY, 5)
    bars = []
    for minute in range(-5, 8):
        ts = start + minute * 60
        high = 500 if minute == -1 else 400 if minute == 5 else 101
        low = 10 if minute == -1 else 20 if minute == 5 else 99
        bars.append(Bar(ts, 100, high, low, 100, 1))
    rng = build_opening_ranges(bars, "ORB-5", 60)[DAY]
    assert rng.complete
    assert rng.expected_bars == 5
    assert rng.high == 101
    assert rng.low == 99
    assert rng.end_ts == end
    # A 15-minute candle starting at 09:30 spills past a 5-minute range.
    fifteen = [Bar(start, 100, 130, 90, 110, 1)]
    spilled = build_opening_ranges(fifteen, "ORB-5", 900)[DAY]
    assert spilled.reason == "bar_does_not_fit_inside_range"
    exact = build_opening_ranges(fifteen, "ORB-15", 900)[DAY]
    assert exact.complete and exact.high == 130 and exact.low == 90
    # Missing an interior minute invalidates the whole range.
    kept = [b for b in bars if b.ts != start + 2 * 60]
    broken = build_opening_ranges(kept, "ORB-5", 60)[DAY]
    assert not broken.complete
    assert broken.reason == "incomplete_range_candles"


def test_close_entry_fills_next_open_and_ignores_the_future():
    rows = [
        (100, 101, 99, 100),       # 09:30 range, high 101 low 99
        (101, 102.5, 100.8, 102),  # 09:35 close confirms
        (102, 102.2, 101.8, 102),  # 09:40 fill, stop not touched
        (102, 103, 102, 102.8),    # 09:45 target
        (102, 9999, 1, 500),       # future noise
    ]
    bars = _bars(DAY, rows)
    trades = run_backtest(bars, _cfg(orb="ORB-5"))
    closed = [t for t in trades if t["status"] == "closed"]
    assert len(closed) == 1
    trade = closed[0]
    assert trade["direction"] == "LONG"
    assert trade["signal_ts"] == bars[1].close_ts(300)
    assert trade["fill_ts"] == bars[2].ts
    assert trade["fill_ts"] >= trade["info_known_ts"]
    assert trade["fill_price"] == pytest.approx(102.0)
    assert trade["exit_reason"] == "take_profit"
    assert trade["path_ambiguous"] is False
    assert trade["ohlc_proxy"] is False
    # A wick through the level with a close back inside is not a close signal.
    wick = _bars(DAY, [(100, 101, 99, 100), (101, 120, 100, 100.5), (100, 100.4, 99.5, 100)])
    assert [t for t in run_backtest(wick, _cfg(orb="ORB-5")) if t["status"] != "unfilled"] == []


def test_no_lookahead_when_a_later_bar_changes():
    rows = [
        (100, 101, 99, 100),
        (101, 102.5, 100.8, 102),
        (102, 102.2, 101.8, 102),
        (102, 103, 102, 102.8),
    ]
    first = run_backtest(_bars(DAY, rows), _cfg(orb="ORB-5"))
    mutated = _bars(DAY, rows)
    mutated.append(Bar(mutated[-1].ts + 300, 1, 99999, 0.1, 50, 1))
    second = run_backtest(mutated, _cfg(orb="ORB-5"))
    a = [t for t in first if t["status"] == "closed"][0]
    b = [t for t in second if t["status"] == "closed"][0]
    assert a["signal_ts"] == b["signal_ts"]
    assert a["fill_price"] == b["fill_price"]
    assert a["net_pnl"] == b["net_pnl"]
    assert a["exit_ts"] == b["exit_ts"]


def test_entry_cutoff_is_1100_and_does_not_flatten_a_position():
    start, _ = range_bounds_ts(DAY, 5)
    cutoff = entry_cutoff_ts(DAY)
    # 18 five-minute bars cover 09:30 inclusive through 10:55. Only the
    # 10:55 bar (which closes exactly at 11:00) breaks the range.
    rows = [(100, 101, 99, 100)] + [(100, 100.5, 99.5, 100)] * 16
    rows.append((100, 130, 100, 130))
    late = run_backtest(_bars(DAY, rows), _cfg(orb="ORB-5"))
    assert late == []
    # 10:50 bar confirms. The next available bar is exactly 11:00, so the
    # order is cancelled rather than filled.
    signal = [
        Bar(start, 100, 101, 99, 100, 1),
        Bar(start + 16 * 300, 100, 120, 100, 120, 1),
        Bar(cutoff, 120, 121, 119, 120, 1),
    ]
    cancelled = run_backtest(signal, _cfg(orb="ORB-5"))
    assert len(cancelled) == 1
    assert cancelled[0]["status"] == "unfilled"
    assert cancelled[0]["unfilled_reason"] == "entry_cutoff"
    # A fill at 10:55 is allowed, and 11:00 does not flatten it.
    held_rows = [(100, 101, 99, 100), (101, 102, 100.5, 102)]
    held_rows += [(102, 102.2, 101.9, 102)] * 20
    held = run_backtest(_bars(DAY, held_rows), _cfg(orb="ORB-5"))
    assert len(held) == 1
    assert held[0]["status"] == "filled_open"
    assert held[0]["exit_reason"] == "open_at_data_end"
    assert held[0]["fill_ts"] < cutoff
    assert held[0]["net_pnl"] is None


def test_intrabar_proxy_gap_and_ambiguous_bar():
    # Open already through the high: do not fill back at the level.
    gap = _bars(DAY, [(100, 101, 99, 100), (110, 112, 109, 111)])
    got = run_backtest(gap, _cfg(orb="ORB-5", entry_model="intrabar"))
    filled = [t for t in got if t["status"] in ("closed", "filled_open")]
    assert len(filled) == 1
    assert filled[0]["raw_entry"] == 110
    assert filled[0]["fill_price"] >= 110
    assert filled[0]["fill_price"] != pytest.approx(101)
    assert filled[0]["ohlc_proxy"] is True
    assert filled[0]["info_known_ts"] > filled[0]["fill_ts"]
    # Both sides of the range on one bar: no fill.
    both = _bars(DAY, [(100, 101, 99, 100), (100, 120, 80, 100)])
    skipped = run_backtest(both, _cfg(orb="ORB-5", entry_model="intrabar"))
    assert skipped[0]["unfilled_reason"] == "ambiguous_both_sides"
    assert all(t["status"] == "unfilled" for t in skipped)


def test_stop_gap_does_not_fill_at_the_stop_price():
    rows = [
        (100, 101, 99, 100),
        (101, 102.5, 100.8, 102),
        (102, 102.2, 101.8, 102),
        (90, 91, 89, 90),
    ]
    trades = run_backtest(_bars(DAY, rows), _cfg(orb="ORB-5"))
    trade = [t for t in trades if t["status"] == "closed"][0]
    assert trade["exit_reason"] == "stop_gap"
    assert trade["raw_exit"] == 90
    assert trade["exit_price"] <= 90


def test_retest_needs_a_later_touch_and_a_continuation_close():
    no_retest = _bars(
        DAY,
        [
            (100, 101, 99, 100),
            (101, 104, 101.5, 103),  # breakout close, no touch of 101
            (103, 105, 102.5, 104),
            (104, 106, 103, 105),
        ],
    )
    assert [t for t in run_backtest(no_retest, _cfg(orb="ORB-5", entry_model="retest")) if t["status"] != "unfilled"] == []
    retest = _bars(
        DAY,
        [
            (100, 101, 99, 100),
            (101, 104, 101.2, 103),   # breakout close
            (103, 103.5, 100.5, 102), # touches 101 and closes back above
            (102, 103, 101.8, 102.5), # fill bar
        ],
    )
    got = run_backtest(retest, _cfg(orb="ORB-5", entry_model="retest"))
    filled = [t for t in got if t["fill_ts"] is not None]
    assert len(filled) == 1
    assert filled[0]["signal_ts"] == retest[2].close_ts(300)
    assert filled[0]["fill_ts"] == retest[3].ts
    assert filled[0]["fill_ts"] >= filled[0]["info_known_ts"]


def test_duplicate_signal_daily_loss_and_position_cap():
    rows = [
        (100, 101, 99, 100),
        (101, 103, 100.8, 102),  # long signal
        (102, 102.2, 90, 100),   # fill and stop
        (100, 100.4, 97, 98),    # short signal
        (98, 99, 97, 98),        # would-be short fill
    ]
    trades = run_backtest(
        _bars(DAY, rows),
        _cfg(orb="ORB-5", max_daily_loss_fraction=0.001, max_trades_per_session=2),
    )
    closed = [t for t in trades if t["status"] == "closed"]
    assert len(closed) == 1 and closed[0]["direction"] == "LONG"
    blocked = [t for t in trades if t["unfilled_reason"] == "max_daily_loss"]
    assert len(blocked) == 1 and blocked[0]["direction"] == "SHORT"
    # Second long is not re-armed after the first long already signaled.
    assert sum(1 for t in trades if t["direction"] == "LONG") == 1


def test_risk_rejects_illegal_size_and_does_not_round_up():
    qty, err = size_contracts(1.0, 0.01, 100_000.0, 99_750.0, 5.0, BTC_USDT)
    assert qty == 0 and err == "below_min_vol"
    qty, err = size_contracts(1000.0, 0.01, 100_000.0, 99_750.0, 900.0, BTC_USDT)
    assert qty == 0 and err == "leverage_outside_contract"
    # Floor, never ceil, at the contract step.
    qty, err = size_contracts(1000.0, 0.01, 100_000.0, 99_750.0, 5.0, BTC_USDT)
    assert err is None and qty == 400
    tiny = _bars(DAY, [(100, 101, 99, 100), (101, 110, 100.5, 110), (110, 111, 109, 110)])
    rejected = run_backtest(tiny, _cfg(orb="ORB-5", starting_equity=0.001))
    assert rejected[0]["status"] == "unfilled"
    assert rejected[0]["unfilled_reason"] == "below_min_vol"


def test_slippage_is_adverse_and_same_bar_target_and_stop_take_the_stop():
    buy = execution_price(100.0, "LONG", "entry", slippage_bps=0.0, spread_usd=0.0, unit=0.1)
    worse = execution_price(100.0, "LONG", "entry", slippage_bps=10.0, spread_usd=0.2, unit=0.1)
    assert worse > buy
    sell = execution_price(100.0, "LONG", "exit", slippage_bps=10.0, spread_usd=0.2, unit=0.1)
    assert sell < 100.0
    bar = Bar(0, 100, 110, 90, 100, 1)
    res = resolve_exit("LONG", 100, 95, 105, bar, in_position_from_open=True)
    assert res.reason == "stop" and res.ambiguous and res.raw_price == 95


def test_split_is_chronological_and_puts_the_tail_out_of_sample():
    days = [DAY + timedelta(days=i) for i in range(10)]
    # Keep only the dates the function will sort; weekends included on purpose.
    splits = chronological_splits(days)
    ordered = sorted(days)
    assert list(splits.values()).count("train") == 6
    assert list(splits.values()).count("validation") == 2
    assert splits[ordered[-1]] == "out_of_sample"
    assert splits[ordered[0]] == "train"


def test_live_path_does_not_submit_and_rejects_a_forming_candle():
    start, _ = range_bounds_ts(DAY, 15)
    session = OrbLiveSession(DAY, orb="ORB-15", bar_seconds=300)
    with pytest.raises(ValueError, match="has not closed"):
        session.on_closed_candle(Bar(start, 100, 101, 99, 100, 1), as_of_ts=start + 10)
    # Three closed 5-minute bars complete ORB-15. The next close breaks high.
    as_of = start + 20 * 300
    for i, close in enumerate((100, 100.2, 100.1, 105)):
        o = 100 if i == 0 else close - 0.2
        session.on_closed_candle(
            Bar(start + i * 300, o, max(o, close) + 0.2, min(o, close) - 0.2, close, 1),
            as_of_ts=as_of,
        )
    signal = session.close_signal()
    assert signal is not None and signal["side"] == "LONG"
    prepared = prepare_market_order(signal, qty=1, stop_price=100.0, target_price=110.0, leverage=5)
    assert prepared.executable
    assert prepared.payload["type"] == 5
    assert prepared.payload["side"] == 1
    assert prepared.payload["openType"] == 1
    assert prepared.payload["vol"] == 1
    with pytest.raises(LiveSubmitDisabled):
        prepared.submit()
    sent = {}

    def _sender(payload):
        sent["payload"] = payload
        return {"ok": True}

    assert prepared.submit(_sender, allow_live_submit=True) == {"ok": True}
    assert sent["payload"]["symbol"] == "BTC_USDT"
    proxy = prepare_market_order(
        {"entry_model": "intrabar", "side": "LONG", "info_known_ts": 1},
        qty=1, stop_price=1, target_price=2, leverage=5,
    )
    assert proxy.executable is False
    with pytest.raises(LiveSubmitDisabled):
        proxy.submit(allow_live_submit=True)
