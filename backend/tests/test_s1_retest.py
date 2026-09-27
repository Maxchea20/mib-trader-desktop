"""S1 retest-and-rejection entry."""
from src.brain.s1_retest import retest_entry, tick_retest, start_retest_watch


def c(ts, o, h, l, cl):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": cl}


LEVEL, ATR = 100.0, 10.0  # retest zone LONG: low <= 102.5, extension cancel: high >= 115


def test_long_retest_hold_and_bullish_close_triggers():
    rows = [c(0, 104, 108, 103, 107),       # no retest yet
            c(60, 106, 106, 101, 101.5),    # retest, holds, but bearish close -> no trigger
            c(120, 101, 104, 100.5, 103)]   # holds + bullish -> trigger at close
    got = retest_entry("LONG", LEVEL, ATR, 0, rows)
    assert got == {"entry_ts": 180, "entry_price": 103}


def test_long_close_below_level_does_not_trigger():
    rows = [c(0, 101, 102, 98, 99.5)]       # retest, bullish body but closed below the level
    assert retest_entry("LONG", LEVEL, ATR, 0, rows) is None


def test_long_extension_before_retest_cancels():
    rows = [c(0, 104, 116, 103, 115)]
    assert retest_entry("LONG", LEVEL, ATR, 0, rows)["cancel"]


def test_extension_after_retest_does_not_cancel():
    rows = [c(0, 102, 103, 101, 101.5), c(60, 101, 116, 101, 115)]
    got = retest_entry("LONG", LEVEL, ATR, 0, rows)
    assert got == {"entry_ts": 120, "entry_price": 115}  # first candle: retest but bearish; second: holds + bullish


def test_short_mirror():
    rows = [c(0, 97, 99, 96, 98.5), c(60, 98.5, 99.5, 97, 97.5)]  # zone: high >= 97.5
    got = retest_entry("SHORT", LEVEL, ATR, 0, rows)
    assert got == {"entry_ts": 120, "entry_price": 97.5}


def test_candles_before_arm_time_are_ignored():
    rows = [c(0, 101, 104, 100.5, 103), c(300, 104, 108, 103, 107)]
    assert retest_entry("LONG", LEVEL, ATR, 300, rows) is None


def test_tick_retest_states():
    st = {}
    start_retest_watch(st, 0, LEVEL, "LONG", ATR)
    assert st["phase"] == "C_WATCH" and st["c_watch"]["mode"] == "retest"
    assert tick_retest(st, [c(0, 104, 108, 103, 107)]) == "NONE"
    assert tick_retest(st, [c(0, 101, 104, 100.5, 103)]) == "SUCCESS"
    assert st["c_watch"]["result"]["entry_price"] == 103
    st2 = {}
    start_retest_watch(st2, 0, LEVEL, "LONG", ATR)
    assert tick_retest(st2, [c(0, 104, 116, 103, 115)]) == "CANCEL"
