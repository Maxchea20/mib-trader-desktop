from src.brain.weather import classify, side_allowed, CHOP, SWING_UP


def _bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1}


def test_short_history_unknown_or_chop():
    out = classify([_bar(i, 1, 2, 0.5, 1) for i in range(5)])
    assert out["flag"] in (CHOP, "UNKNOWN")


def test_side_allowed_swing_up_blocks_short():
    assert side_allowed(SWING_UP, "LONG")
    assert not side_allowed(SWING_UP, "SHORT")
    assert side_allowed(CHOP, "SHORT")
