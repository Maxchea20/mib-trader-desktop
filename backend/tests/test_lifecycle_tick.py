from src.brain.lifecycle import HOLD, TRAIL, EXIT
from src.brain.lifecycle_tick import tick_5m, position_from_open_trade, si_checklist
from src.contract import LONG, SHORT


def _trade(side=LONG, entry=100.0, sl=99.0, tp=102.0):
    return {
        "id": "t1",
        "side": side,
        "entry_price": entry,
        "sl_price": sl,
        "tp_price": tp,
        "opened_at": 1,
    }


def test_tick_hold():
    out = tick_5m(_trade(), price=100.2, high=100.3, low=100.1)
    assert out["action"] == HOLD


def test_tick_exit_on_choch():
    out = tick_5m(_trade(LONG), price=100.1, high=100.2, low=100.0,
                  structure_event="CHoCH", structure_dir="BEARISH")
    assert out["action"] == EXIT


def test_tick_trail_after_1r():
    out = tick_5m(_trade(LONG, sl=99.0, tp=102.0), price=101.2, high=101.2, low=100.8)
    assert out["action"] == TRAIL
    assert out.get("sl", 0) >= 100.0


def test_position_from_open_trade_side():
    pos = position_from_open_trade(_trade(SHORT, entry=100.0, sl=101.0, tp=98.0))
    assert pos.side == SHORT
    assert pos.entry == 100.0


def test_si_skips_fire_bar():
    out = si_checklist(
        side="LONG", opened_at=1_700_000_000, bar_15m_ts=1_700_000_000,
        close=99.0, choch_against=True, parent=101.0, level=100.5,
    )
    assert out["new_15m"] is False
    assert out["kill_choch"] is False
    assert out["kill_struct"] is False


def test_si_one_check_alone_does_not_kill():
    out = si_checklist(
        side="LONG", opened_at=1, bar_15m_ts=900,
        close=99.4, choch_against=False, parent=98.0, level=100.0,
    )
    assert out["beyond_level"] is True
    assert out["beyond_parent"] is False
    assert out["kill_struct"] is False
    assert out["kill_choch"] is False


def test_si_parent_and_level_together_kill():
    out = si_checklist(
        side="LONG", opened_at=1, bar_15m_ts=900,
        close=97.0, choch_against=False, parent=98.0, level=100.0,
    )
    assert out["kill_struct"] is True
    assert out["kill_choch"] is False


def test_si_choch_against_kills_without_price_checks():
    out = si_checklist(
        side="LONG", opened_at=1, bar_15m_ts=900,
        close=100.2, choch_against=True, parent=98.0, level=99.0,
    )
    assert out["kill_choch"] is True
    assert out["kill_struct"] is False
