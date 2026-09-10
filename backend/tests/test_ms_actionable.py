"""Unit tests for the MS actionable layer.

W=5 book is not under test here. These cover the 1-bar state machine
and the M5 HOLD/FAIL horizon (closed M5 only).
"""
from types import SimpleNamespace

from src.market_state.actionable import apply_actionable_structure
from src.brain.evidence import extract_state, classify_state
from src.market_data.closed_candles import candle_is_closed, filter_closed


def _struct(event="BOS", direction="LONG", level=2000.0):
    return SimpleNamespace(
        event=SimpleNamespace(event=event, direction=direction, reference_price=level),
        actionable_state="NONE",
        actionable_level=None,
        actionable_distance_atr=0.0,
        m5_confirm="NONE",
        developing_high=False,
        developing_low=False,
    )


def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def test_closed_candle_predicate():
    assert candle_is_closed(1_000, "15m", now=1_000 + 900) is True
    assert candle_is_closed(1_000, "15m", now=1_000 + 899) is False
    assert candle_is_closed(1_000, "5m", now=1_000 + 300) is True
    assert candle_is_closed(1_000, "5m", now=1_000 + 299) is False


def test_filter_closed_drops_forming_bar():
    bars = [_bar(0, 1, 2, 0, 1), _bar(900, 1, 2, 0, 1)]
    out = filter_closed(bars, "15m", now=900 + 899)
    assert [b["ts"] for b in out] == [0]


def test_continuation_long():
    s = _struct()
    candles = [_bar(i * 900, 2010, 2012, 2008, 2010) for i in range(5)]
    apply_actionable_structure(s, candles, atr=10.0)
    assert s.actionable_state == "CONTINUATION"
    assert s.actionable_level == 2000.0


def test_pullback_long():
    s = _struct()
    # low reaches 0.5 ATR of the level, close still above
    candles = [_bar(i * 900, 2006, 2008, 2005, 2006) for i in range(4)]
    candles.append(_bar(3600, 2006, 2007, 2004.5, 2006))
    apply_actionable_structure(s, candles, atr=10.0)
    assert s.actionable_state == "PULLBACK"


def test_retest_long():
    s = _struct()
    candles = [_bar(i * 900, 2003, 2004, 2001, 2002) for i in range(5)]
    apply_actionable_structure(s, candles, atr=10.0)
    assert s.actionable_state == "RETEST"


def test_failure_long():
    s = _struct()
    candles = [_bar(i * 900, 1995, 1998, 1990, 1994) for i in range(5)]
    apply_actionable_structure(s, candles, atr=10.0)
    assert s.actionable_state == "FAILURE"


def test_reversal_on_choch_through():
    s = _struct(event="CHoCH", direction="LONG", level=2000.0)
    candles = [_bar(i * 900, 1995, 1998, 1990, 1994) for i in range(5)]
    apply_actionable_structure(s, candles, atr=10.0)
    assert s.actionable_state == "REVERSAL"


def test_m5_hold_and_fail():
    s = _struct()
    ts = 10_000
    candles = [_bar(ts - 900 * i, 2010, 2012, 2008, 2010) for i in range(5, 0, -1)]
    candles.append(_bar(ts, 2010, 2012, 2008, 2010))
    m5_hold = [_bar(ts + 0, 2010, 2011, 2009, 2010), _bar(ts + 300, 2010, 2011, 2009, 2008)]
    apply_actionable_structure(s, candles, atr=10.0, m5_candles=m5_hold, timeframe="15m")
    assert s.m5_confirm == "HOLD"

    s2 = _struct()
    m5_fail = [_bar(ts + 0, 2010, 2011, 2009, 2010), _bar(ts + 300, 2002, 2003, 1995, 1996)]
    apply_actionable_structure(s2, candles, atr=10.0, m5_candles=m5_fail, timeframe="15m")
    assert s2.m5_confirm == "FAIL"


def test_m5_lookahead_rejected():
    s = _struct()
    ts = 10_000
    candles = [_bar(ts, 2010, 2012, 2008, 2010)]
    # M5 that closes AFTER the M15 horizon must not vote
    future = [_bar(ts + 900, 1990, 1991, 1988, 1989)]
    apply_actionable_structure(s, candles, atr=10.0, m5_candles=future, timeframe="15m")
    assert s.m5_confirm == "NONE"


def test_no_event_is_none():
    s = SimpleNamespace(
        event=SimpleNamespace(event="NONE", direction="NEUTRAL", reference_price=None),
        actionable_state="X",
        m5_confirm="X",
    )
    apply_actionable_structure(s, [_bar(0, 1, 1, 1, 1)], atr=10.0)
    assert s.actionable_state == "NONE"
    assert s.m5_confirm == "NONE"


def test_brain_reads_state_line():
    ev = ["Actionable: RETEST | Level: 2000.0 | Distance: 0.10 ATR | M5: HOLD", "State: RETEST"]
    assert extract_state(ev) == "RETEST"
    cls = classify_state("RETEST")
    assert cls["trigger"] == 1.0
    assert classify_state("FAILURE")["negative"] == 1.0
    assert classify_state("PULLBACK")["context_only"] == 1.0
