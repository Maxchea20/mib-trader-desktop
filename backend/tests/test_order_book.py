"""MEXC live order-book engine + ORDER_BOOK observation."""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.market_data.order_book import OrderBook, parse_mexc_depth_message, STALE_AFTER_SEC
from src.order_book.observe import observe
from src.observation import to_agent_result_compat

def test_snapshot_best_spread_mid():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[100.0, 5, 1], [99.5, 2, 1]], asks=[[100.5, 3, 1], [101.0, 4, 1]], version=1, exchange_ts_ms=1700000000000)
    assert book.best_bid() == 100.0
    assert book.best_ask() == 100.5
    assert book.mid() == pytest.approx(100.25)
    assert book.spread() == pytest.approx(0.5)
    assert book.book_valid()

def test_incremental_update_and_delete():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[100.0, 5]], asks=[[101.0, 2]], version=10, exchange_ts_ms=1)
    ok = book.apply_delta(bids=[[100.0, 0], [99.0, 8]], asks=[[101.0, 7]], version=11, exchange_ts_ms=2)
    assert ok and 100.0 not in book.bids and book.bids[99.0] == 8

def test_version_gap_rejected():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[100, 1]], asks=[[101, 1]], version=10, exchange_ts_ms=1)
    assert book.apply_delta(bids=[[99, 1]], asks=[], version=14, exchange_ts_ms=2) is False

def test_malformed_rows_ignored():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[["x", 1], [100, 2], None], asks=[[101, 3], []], version=1, exchange_ts_ms=1)
    assert book.best_bid() == 100 and book.best_ask() == 101

def test_crossed_book():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[102, 1]], asks=[[101, 1]], version=1, exchange_ts_ms=1)
    assert book.crossed() and book.book_valid() is False

def test_stale_and_disconnected():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[100, 1]], asks=[[101, 1]], version=1, exchange_ts_ms=1)
    book.local_ts = time.time() - STALE_AFTER_SEC - 1
    assert book.stale()
    book.mark_disconnected()
    assert book.book_valid() is False

def test_parse_push_depth():
    p = parse_mexc_depth_message({"channel": "push.depth", "data": {"asks": [[6859.5, 3251, 1]], "bids": [[6858.0, 10, 2]], "version": 96801927}, "symbol": "BTC_USDT", "ts": 1587442022003})
    assert p["version"] == 96801927 and p["snapshot"] is False

def test_parse_full_is_snapshot():
    assert parse_mexc_depth_message({"channel": "push.depth.full", "data": {"bids": [], "asks": []}})["snapshot"] is True

def test_imbalance_bands():
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[100.0, 10], [99.0, 100]], asks=[[100.2, 10], [101.0, 100]], version=1, exchange_ts_ms=1)
    assert book.imbalance(0.10) is not None

def test_observation_live_and_backtest():
    empty = observe(None)
    assert empty.valid is False and empty.state == "UNAVAILABLE" and empty.flags["live_only"]
    book = OrderBook("BTC_USDT")
    book.apply_snapshot(bids=[[100, 2]], asks=[[100.5, 3]], version=3, exchange_ts_ms=99)
    obs = observe(book)
    assert obs.valid and obs.measurement("best_bid") == 100
    json.dumps(obs.to_dict())
    with pytest.raises(NotImplementedError):
        to_agent_result_compat(obs)
