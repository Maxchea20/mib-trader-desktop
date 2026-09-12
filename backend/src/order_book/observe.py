"""ORDER_BOOK AnalysisObservation — live MEXC depth only."""
from typing import Optional

from ..market_data.order_book import BAND_PCTS, OrderBook
from ..observation import AnalysisObservation, Measurement, Provenance


AGENT_ID = "mexc_order_book"


def observe(book: Optional[OrderBook], timeframe: str = "live") -> AnalysisObservation:
    def _prov(**extra):
        kw = dict(
            source_module="market_data.order_book",
            timeframe=timeframe,
            detection_timestamp=0,
            source_calculation="mexc.sub.depth.full+rest.depth",
        )
        kw.update(extra)
        return Provenance(**kw)

    if book is None:
        return AnalysisObservation(
            source="mexc",
            observation_type="ORDER_BOOK",
            timeframe=timeframe,
            provenance=_prov(),
            state="UNAVAILABLE",
            flags={"connected": False, "book_valid": False, "stale": True, "crossed": False, "has_depth": False, "live_only": True},
            notes=["No live order book (backtest or disconnected)"],
            valid=False,
        )

    det = book.exchange_ts_ms
    measurements = []
    bb, ba, mid = book.best_bid(), book.best_ask(), book.mid()
    if bb is not None:
        measurements.append(Measurement("best_bid", float(bb), unit="price", origin="mexc"))
    if ba is not None:
        measurements.append(Measurement("best_ask", float(ba), unit="price", origin="mexc"))
    if mid is not None:
        measurements.append(Measurement("mid_price", float(mid), unit="price", origin="mexc"))
    sp = book.spread()
    if sp is not None:
        measurements.append(Measurement("spread", float(sp), unit="price", origin="mexc"))
    spp = book.spread_pct()
    if spp is not None:
        measurements.append(Measurement("spread_pct", float(spp), unit="pct", origin="mexc"))
    for pct in BAND_PCTS:
        key = str(pct).replace(".", "p")
        measurements.append(Measurement(f"bid_depth_{key}pct", book.depth_within_pct("bid", pct), unit="qty", origin="mexc"))
        measurements.append(Measurement(f"ask_depth_{key}pct", book.depth_within_pct("ask", pct), unit="qty", origin="mexc"))
        imb = book.imbalance(pct)
        if imb is not None:
            measurements.append(Measurement(f"imbalance_{key}pct", float(imb), unit="ratio", origin="mexc"))

    valid = book.book_valid()
    state = "VALID" if valid else ("CROSSED" if book.crossed() else ("STALE" if book.stale() else "INVALID"))
    flags = {
        "connected": bool(book.connected),
        "book_valid": bool(valid),
        "stale": bool(book.stale()),
        "crossed": bool(book.crossed()),
        "has_depth": bool(book.bids or book.asks),
        "live_only": True,
    }
    notes = [f"symbol={book.symbol} version={book.version} exchange_ts_ms={book.exchange_ts_ms}"]
    return AnalysisObservation(
        source="mexc",
        observation_type="ORDER_BOOK",
        timeframe=timeframe,
        provenance=_prov(detection_timestamp=det, event_timestamp=det, candle_timestamp=None, price_at_detection=mid),
        state=state,
        measurements=measurements,
        flags=flags,
        tags=["ORDER_BOOK", "MEXC", "LIVE_ONLY"],
        notes=notes,
        valid=valid,
    )
