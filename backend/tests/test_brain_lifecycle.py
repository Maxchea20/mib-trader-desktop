from src.brain.lifecycle import (
    HOLD, TRAIL, EXIT, LIFECYCLE_VERSION,
    size_from_risk, position_from_fire, reevaluate, thesis_from_fire,
)
from src.contract import LONG, SHORT


def _fire(side=LONG, entry=100.0, sl=99.0, tp=102.0, atr=1.0):
    return {
        "direction": side,
        "entry": entry,
        "stop": sl,
        "target": tp,
        "atr_15m": atr,
        "event": "BOS",
        "why_state": ["15m structure BOS", "M5 held level"],
        "hunt": {"level": entry},
    }


def test_size_from_risk_not_arbitrary():
    s = size_from_risk(1000.0, 0.02, 100.0, 99.0)
    assert s["risk_usd"] == 20.0
    assert abs(s["qty"] - 20.0) < 1e-9
    assert abs(s["notional"] - 2000.0) < 1e-9


def test_thesis_from_fire():
    th = thesis_from_fire(_fire())
    assert th.side == LONG
    assert th.event == "BOS"
    assert th.valid is True


def test_hold_when_nothing_changed():
    pos = position_from_fire(_fire(), trade_id="t1", equity=1000, risk_pct=0.02)
    out = reevaluate(pos, price=100.2)
    assert out["action"] == HOLD
    assert out["lifecycle_version"] == LIFECYCLE_VERSION


def test_exit_on_choch_against():
    pos = position_from_fire(_fire(LONG), trade_id="t2", equity=1000, risk_pct=0.02)
    out = reevaluate(pos, price=100.1, structure_event="CHoCH", structure_dir="BEARISH")
    assert out["action"] == EXIT
    assert out["exit_kind"] == "THESIS_FAILURE"
    assert pos.thesis.valid is False


def test_exit_on_1h_flip():
    pos = position_from_fire(_fire(LONG), trade_id="t3", equity=1000, risk_pct=0.02)
    out = reevaluate(pos, price=100.4, trend_1h_state="STRONG_BEAR")
    assert out["action"] == EXIT
    assert "1h" in out["reason"]


def test_trail_after_one_r():
    pos = position_from_fire(_fire(LONG, sl=99.0, atr=1.0), trade_id="t4", equity=1000, risk_pct=0.02)
    out = reevaluate(pos, price=101.2, high=101.2)
    assert out["action"] == TRAIL
    assert pos.protected is True
    assert pos.hard_sl() >= 100.0


def test_short_trail_never_loosens():
    pos = position_from_fire(_fire(SHORT, entry=100.0, sl=101.0, tp=98.0, atr=1.0), trade_id="t5", equity=1000, risk_pct=0.02)
    reevaluate(pos, price=98.8, low=98.8)
    sl1 = pos.hard_sl()
    reevaluate(pos, price=99.5, low=98.8)
    sl2 = pos.hard_sl()
    assert sl2 <= sl1
