"""Unit tests for backend/src/autotrader.py position sizing.

Tests the sizing MATH in compute_sizing(), the STATE["normal_base"] capture
triggers (lazy first-use, COMPOUNDING->NORMAL switch, manual Refresh), and
the margin safety gate — by mocking the two network calls involved:
  - src.market_data.mexc_market_data.rest_get   (public contract detail)
  - src.market_data.mexc_private.get_assets     (private balance)
No live MEXC credentials or network access are required to run these.

Nothing here touches Hunt C-FI, weather gate, SL/TP, or lifecycle logic —
those modules aren't imported or exercised by these tests.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# autotrader.update()/status() call _open_auto(), which reads the paper_trades
# table via src.paper_trading — point it at a throwaway sqlite file so these
# sizing-config tests don't touch real trade data.
os.environ.setdefault("MARKET_DB_PATH", os.path.join(tempfile.mkdtemp(), "test_sizing.sqlite"))

from src import autotrader  # noqa: E402
from src import paper_trading  # noqa: E402

paper_trading.init_db()


FAKE_CONTRACT_DETAIL = {
    "success": True,
    "code": 0,
    "data": {
        "symbol": "BTC_USDT",
        "contractSize": 0.0001,
        "volUnit": 1,
        "minVol": 1,
        "maxVol": 5000000,
    },
}

FAKE_PRICE = 50000.0  # illustrative BTC_USDT price used across tests
BALANCE = 18.30        # the user's actual current MEXC balance for this test round


def _reset_all():
    autotrader.CONFIG.update({
        "sizing_mode": "NORMAL",
        "allocation_pct": 20.0,
        "leverage": 20.0,
        "max_live_notional_usd": 1000.0,
    })
    autotrader.STATE["normal_base"] = None
    autotrader.STATE["normal_base_captured_at"] = None


def _fake_candles(*_a, **_k):
    return [{"ts": 0, "close": FAKE_PRICE}]


def _fake_assets(balance):
    return [{"currency": "USDT", "availableBalance": balance, "equity": balance + 5000}]  # equity deliberately different


# ---------------------------------------------------------------------------
# The exact test case from the spec: $18.30 / 20% / 20x
# ---------------------------------------------------------------------------

@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_normal_1830_20pct_20x_matches_spec_exactly(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()  # lazy first-use capture happens here
    assert s["error"] is None
    assert s["sizing_mode"] == "NORMAL"
    assert s["normal_base"] == 18.30
    assert s["balance_used"] == 18.30
    assert round(s["calculated_capital"], 2) == 3.66     # 18.30 * 20%
    assert round(s["calculated_notional"], 2) == 73.20    # 3.66 * 20x
    assert round(s["final_notional"], 2) == 73.20
    assert s["capped"] is False


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_all_four_leverage_presets_with_1830_base_20pct(mock_detail, mock_candles):
    """Regression guard: leverage must reach the formula exactly as selected —
    no leftover hardcoded 10x anywhere in the path."""
    _reset_all()
    expected = {10: 36.60, 20: 73.20, 50: 183.00, 100: 366.00}
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        for lev, expected_notional in expected.items():
            autotrader.CONFIG["leverage"] = float(lev)
            s = autotrader.compute_sizing()
            assert round(s["calculated_notional"], 2) == expected_notional, f"leverage={lev}"


# ---------------------------------------------------------------------------
# normal_base capture triggers
# ---------------------------------------------------------------------------

@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_lazy_first_use_captures_base(mock_detail, mock_candles):
    _reset_all()
    assert autotrader.STATE["normal_base"] is None
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)) as mock_assets:
        s = autotrader.compute_sizing()
    mock_assets.assert_called_once()
    assert autotrader.STATE["normal_base"] == 18.30
    assert s["normal_base"] == 18.30


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_normal_base_ignores_subsequent_real_balance_changes(mock_detail, mock_candles):
    """Core requirement: once captured, normal_base must NOT track the live
    account through $20, $25, $30 — only Refresh or a mode-switch moves it."""
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(18.30)):
        s1 = autotrader.compute_sizing()
    for new_balance in (20.0, 25.0, 30.0):
        with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(new_balance)) as mock_assets:
            s = autotrader.compute_sizing()
        mock_assets.assert_not_called()  # normal_base already set -> no fetch at all
        assert s["balance_used"] == 18.30
        assert round(s["calculated_notional"], 2) == 73.20
        assert round(s["calculated_notional"], 2) != 80.00
        assert round(s["calculated_notional"], 2) != 100.00
        assert round(s["calculated_notional"], 2) != 120.00


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_compounding_to_normal_transition_recaptures_base(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(18.30)):
        autotrader.compute_sizing()
    assert autotrader.STATE["normal_base"] == 18.30

    autotrader.CONFIG["sizing_mode"] = "COMPOUNDING"
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(25.0)):
        autotrader.update({"sizing_mode": "NORMAL"})  # goes through update(), not compute_sizing()
    assert autotrader.STATE["normal_base"] == 25.0


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_manual_refresh_recaptures_base(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(18.30)):
        autotrader.compute_sizing()
    assert autotrader.STATE["normal_base"] == 18.30

    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(30.0)):
        autotrader.update({"refresh_normal_base": True})
    assert autotrader.STATE["normal_base"] == 30.0


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_staying_in_normal_does_not_recapture(mock_detail, mock_candles):
    """Re-selecting NORMAL while already in NORMAL (no actual transition) must
    NOT silently re-fetch — only a genuine COMPOUNDING->NORMAL switch does."""
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(18.30)):
        autotrader.compute_sizing()
    assert autotrader.STATE["normal_base"] == 18.30

    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(999.0)) as mock_assets:
        autotrader.update({"sizing_mode": "NORMAL"})  # already NORMAL -> not a transition
    mock_assets.assert_not_called()
    assert autotrader.STATE["normal_base"] == 18.30


# ---------------------------------------------------------------------------
# COMPOUNDING mode — fresh balance every call
# ---------------------------------------------------------------------------

@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_compounding_scales_with_balance_per_spec(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["sizing_mode"] = "COMPOUNDING"
    cases = [(18.30, 3.66, 73.20), (20.0, 4.00, 80.00), (25.0, 5.00, 100.00), (30.0, 6.00, 120.00)]
    for bal, expected_capital, expected_notional in cases:
        with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(bal)) as mock_assets:
            s = autotrader.compute_sizing()
        mock_assets.assert_called_once()  # fresh fetch EVERY call, never cached
        assert round(s["calculated_capital"], 2) == expected_capital, f"balance={bal}"
        assert round(s["calculated_notional"], 2) == expected_notional, f"balance={bal}"


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_compounding_uses_available_balance_not_equity(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["sizing_mode"] = "COMPOUNDING"
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(18.30)):
        s = autotrader.compute_sizing()
    assert s["balance_used"] == 18.30  # not 5018.30 (the fake equity value)


# ---------------------------------------------------------------------------
# Leverage applied exactly once / no hardcoded 10x
# ---------------------------------------------------------------------------

@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_leverage_applied_exactly_once(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["calculated_capital"] == s["balance_used"] * s["allocation_pct"] / 100.0
    assert round(s["calculated_notional"], 4) == round(s["calculated_capital"] * s["leverage"], 4)
    assert round(s["required_margin"], 4) == round(s["final_notional"] / s["leverage"], 4)


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_open_live_from_hunt_passes_selected_leverage_not_hardcoded_10(mock_detail, mock_candles):
    """Confirms the live submit_order() call receives CONFIG['leverage'], not
    a literal 10 — this is the actual regression the correction was about."""
    _reset_all()
    autotrader.CONFIG["leverage"] = 20.0
    hunt = {"direction": "LONG", "entry": FAKE_PRICE, "stop": FAKE_PRICE * 0.99, "target": FAKE_PRICE * 1.02}
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)), \
         patch("src.market_data.mexc_private.submit_order", return_value={"success": True, "data": "order123"}) as mock_submit:
        autotrader._open_live_from_hunt(hunt, "15m", FAKE_PRICE)
    _, kwargs = mock_submit.call_args
    assert kwargs["leverage"] == 20  # NOT 10


# ---------------------------------------------------------------------------
# Max notional cap
# ---------------------------------------------------------------------------

@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_max_notional_cap_applied(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["max_live_notional_usd"] = 50.0  # below the $73.20 calculated notional
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert round(s["calculated_notional"], 2) == 73.20
    assert s["final_notional"] == 50.0
    assert s["capped"] is True


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_no_cap_when_under_limit(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["max_live_notional_usd"] = 1000.0
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["capped"] is False
    assert round(s["final_notional"], 2) == round(s["calculated_notional"], 2) == 73.20


# ---------------------------------------------------------------------------
# Contract quantity rounding / validation
# ---------------------------------------------------------------------------

@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value={
    "success": True, "data": {"contractSize": 0.0001, "volUnit": 1, "minVol": 500, "maxVol": 5000000},
})
def test_below_min_vol_is_rejected(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["max_live_notional_usd"] = 5.0  # forces a tiny quantity
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is not None
    assert "minVol" in s["error"]


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value={
    "success": True, "data": {"contractSize": 0.0001, "volUnit": 1, "minVol": 1, "maxVol": 10,
                               "minLeverage": 1, "maxLeverage": 10000},
})
def test_above_max_vol_is_rejected(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["max_live_notional_usd"] = 1000000.0
    autotrader.CONFIG["allocation_pct"] = 100.0
    autotrader.CONFIG["leverage"] = 1000.0  # within this test's maxLeverage=10000, isolates the maxVol check
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is not None
    assert "maxVol" in s["error"]


# ---------------------------------------------------------------------------
# Margin safety check (_check_available_margin) — independent of sizing_mode
# ---------------------------------------------------------------------------

def test_insufficient_balance_is_rejected_with_buffer():
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(10.0)):
        check = autotrader._check_available_margin(required_margin=50.0)
    assert check["ok"] is False
    assert "insufficient" in check["reason"].lower()


def test_sufficient_balance_with_buffer_passes():
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(51.0)):
        check = autotrader._check_available_margin(required_margin=50.0)
    assert check["ok"] is True


def test_balance_just_under_buffer_is_rejected():
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(50.99)):
        check = autotrader._check_available_margin(required_margin=50.0)
    assert check["ok"] is False


def test_margin_check_always_fetches_fresh_regardless_of_sizing_mode():
    _reset_all()  # NORMAL mode
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(1.0)) as mock_assets:
        autotrader._check_available_margin(required_margin=50.0)
    mock_assets.assert_called_once()


def test_order_rejected_when_insufficient_balance_submit_not_called():
    """Isolates the margin-check rejection specifically: sizing itself must
    succeed (valid quantity, above minVol) so this actually exercises
    _check_available_margin's own separate fetch, not a minVol rejection."""
    _reset_all()
    hunt = {"direction": "LONG", "entry": FAKE_PRICE, "stop": FAKE_PRICE * 0.99, "target": FAKE_PRICE * 1.02}
    # First get_assets() call is compute_sizing()'s lazy normal_base capture
    # (needs to be big enough to produce valid, above-minVol sizing); the
    # second is _check_available_margin's independent fresh fetch, made too
    # small on purpose so the margin gate itself is what rejects the order.
    with patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles), \
         patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL), \
         patch("src.market_data.mexc_private.get_assets", side_effect=[_fake_assets(BALANCE), _fake_assets(0.01)]), \
         patch("src.market_data.mexc_private.submit_order") as mock_submit:
        try:
            autotrader._open_live_from_hunt(hunt, "15m", FAKE_PRICE)
            raised = False
        except RuntimeError as e:
            raised = True
            err_msg = str(e)
    assert raised is True
    assert "insufficient" in err_msg.lower()
    mock_submit.assert_not_called()


# ---------------------------------------------------------------------------
# update() / config persistence
# ---------------------------------------------------------------------------

def test_update_persists_sizing_fields():
    _reset_all()
    autotrader.update({
        "allocation_pct": 25.0,
        "leverage": 50.0,
        "max_live_notional_usd": 5000.0,
    })
    assert autotrader.CONFIG["allocation_pct"] == 25.0
    assert autotrader.CONFIG["leverage"] == 50.0
    assert autotrader.CONFIG["max_live_notional_usd"] == 5000.0


def test_update_rejects_invalid_allocation_pct():
    _reset_all()
    before = autotrader.CONFIG["allocation_pct"]
    autotrader.update({"allocation_pct": 150.0})
    assert autotrader.CONFIG["allocation_pct"] == before


def test_update_rejects_zero_or_negative_leverage():
    _reset_all()
    before = autotrader.CONFIG["leverage"]
    autotrader.update({"leverage": 0})
    assert autotrader.CONFIG["leverage"] == before
    autotrader.update({"leverage": -5})
    assert autotrader.CONFIG["leverage"] == before


def test_update_rejects_invalid_sizing_mode():
    _reset_all()
    autotrader.update({"sizing_mode": "BANANA"})
    assert autotrader.CONFIG["sizing_mode"] == "NORMAL"


def test_reference_balance_field_no_longer_exists_in_config():
    """Regression guard for the correction: reference_balance must be gone."""
    _reset_all()
    assert "reference_balance" not in autotrader.CONFIG


def test_notional_usd_untouched_by_sizing_changes():
    """PAPER-path field stays a separate config key, unaffected by sizing edits."""
    _reset_all()
    before = autotrader.CONFIG["notional_usd"]
    autotrader.update({"allocation_pct": 33.0, "leverage": 50.0, "sizing_mode": "COMPOUNDING"})
    assert autotrader.CONFIG["notional_usd"] == before


# ---------------------------------------------------------------------------
# MEXC order-input validation: tick-size snapping, leverage bounds,
# contract tradability, fresh price at submit, silent-rejection warnings
# ---------------------------------------------------------------------------

def test_snap_to_tick_rounds_to_nearest_valid_tick():
    # priceUnit=0.5 -> must land on a multiple of 0.5
    assert autotrader._snap_to_tick(76892.83, 0.5, 2) == 76893.0
    assert autotrader._snap_to_tick(76892.2, 0.5, 2) == 76892.0
    assert autotrader._snap_to_tick(76892.75, 0.5, 2) == 76893.0  # rounds to nearest


def test_snap_to_tick_no_price_unit_just_rounds_scale():
    assert autotrader._snap_to_tick(76892.8371, 0, 2) == 76892.84


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_leverage_within_bounds_reported_in_sizing(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is None
    assert s["min_leverage"] == 1  # FAKE_CONTRACT_DETAIL has no minLeverage/maxLeverage -> defaults
    assert s["max_leverage"] == 125


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value={
    "success": True, "data": {"contractSize": 0.0001, "volUnit": 1, "minVol": 1, "maxVol": 5000000,
                               "minLeverage": 1, "maxLeverage": 50},
})
def test_leverage_above_contract_max_is_rejected(mock_detail, mock_candles):
    _reset_all()
    autotrader.CONFIG["leverage"] = 100.0  # a real preset button, but exceeds this contract's max=50
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is not None
    assert "leverage" in s["error"].lower()
    assert "50" in s["error"]


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value={
    "success": True, "data": {"contractSize": 0.0001, "volUnit": 1, "minVol": 1, "maxVol": 5000000,
                               "state": 3},  # 3 = offline
})
def test_contract_not_tradable_state_is_rejected(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is not None
    assert "not currently tradable" in s["error"]


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value={
    "success": True, "data": {"contractSize": 0.0001, "volUnit": 1, "minVol": 1, "maxVol": 5000000,
                               "apiAllowed": False},
})
def test_api_not_allowed_is_rejected(mock_detail, mock_candles):
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is not None
    assert "apiAllowed" in s["error"]


@patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles)
@patch("src.market_data.mexc_market_data.rest_get", return_value=FAKE_CONTRACT_DETAIL)
def test_contract_state_missing_field_does_not_block(mock_detail, mock_candles):
    """state/apiAllowed missing from the response (not explicitly bad) must
    NOT block sizing — only an explicit non-zero state / apiAllowed=false does."""
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)):
        s = autotrader.compute_sizing()
    assert s["error"] is None


def test_fresh_price_used_over_stale_cached_price():
    """_open_live_from_hunt must use a freshly-fetched ticker price for both
    sizing and submission, not the loop's possibly-stale live_price, when the
    fresh fetch succeeds."""
    _reset_all()
    hunt = {"direction": "LONG", "entry": 50000.0, "stop": 49500.0, "target": 51000.0}
    fresh_ticker = {"success": True, "data": {"lastPrice": 50123.45}}
    stale_price = 49000.0  # deliberately different from fresh, to prove which one wins

    def fake_rest_get(path):
        if "ticker" in path:
            return fresh_ticker
        return FAKE_CONTRACT_DETAIL

    with patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles), \
         patch("src.market_data.mexc_market_data.rest_get", side_effect=fake_rest_get), \
         patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)), \
         patch("src.market_data.mexc_private.submit_order", return_value={"success": True, "data": "x"}) as mock_submit:
        autotrader._open_live_from_hunt(hunt, "15m", stale_price)
    _, kwargs = mock_submit.call_args
    assert kwargs["price"] == 50123.45  # the fresh ticker price, not 49000.0


def test_price_sl_tp_snapped_to_tick_before_submit():
    _reset_all()
    hunt = {"direction": "LONG", "entry": 50000.33, "stop": 49500.17, "target": 51000.61}
    detail_with_tick = {
        "success": True,
        "data": {"contractSize": 0.0001, "volUnit": 1, "minVol": 1, "maxVol": 5000000,
                  "priceUnit": 0.5, "priceScale": 1},
    }

    def fake_rest_get(path):
        if "ticker" in path:
            return {"success": True, "data": {"lastPrice": 50000.33}}
        return detail_with_tick

    with patch("src.autotrader.dao.read_closed_candles", side_effect=_fake_candles), \
         patch("src.market_data.mexc_market_data.rest_get", side_effect=fake_rest_get), \
         patch("src.market_data.mexc_private.get_assets", return_value=_fake_assets(BALANCE)), \
         patch("src.market_data.mexc_private.submit_order", return_value={"success": True, "data": "x"}) as mock_submit:
        autotrader._open_live_from_hunt(hunt, "15m", 50000.33)
    _, kwargs = mock_submit.call_args
    # every submitted price must be an exact multiple of 0.5
    for field in ("price", "stop_loss_price", "take_profit_price"):
        assert (kwargs[field] * 2) == int(kwargs[field] * 2), f"{field}={kwargs[field]} not on a 0.5 tick"


# ---------------------------------------------------------------------------
# update() no longer silently swallows rejected fields — now reports warnings
# ---------------------------------------------------------------------------

def test_update_reports_warning_for_rejected_allocation():
    _reset_all()
    before = autotrader.CONFIG["allocation_pct"]
    result = autotrader.update({"allocation_pct": 150.0})
    assert autotrader.CONFIG["allocation_pct"] == before
    assert any("allocation_pct" in w for w in result["warnings"])


def test_update_reports_warning_for_rejected_leverage():
    _reset_all()
    result = autotrader.update({"leverage": -5})
    assert any("leverage" in w for w in result["warnings"])


def test_update_reports_warning_for_rejected_max_notional():
    _reset_all()
    result = autotrader.update({"max_live_notional_usd": -100})
    assert any("max_live_notional_usd" in w for w in result["warnings"])


def test_update_reports_warning_for_invalid_sizing_mode():
    _reset_all()
    result = autotrader.update({"sizing_mode": "BANANA"})
    assert any("sizing_mode" in w for w in result["warnings"])


def test_update_no_warnings_on_valid_input():
    _reset_all()
    result = autotrader.update({"allocation_pct": 15.0, "leverage": 50.0})
    assert result["warnings"] == []


def test_update_reports_warning_when_refresh_fails():
    _reset_all()
    with patch("src.market_data.mexc_private.get_assets", side_effect=RuntimeError("network down")):
        result = autotrader.update({"refresh_normal_base": True})
    assert any("refresh_normal_base" in w for w in result["warnings"])