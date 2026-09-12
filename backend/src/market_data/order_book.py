"""Live MEXC Futures order book.

Protocol (contract.mexc.com):
  REST  GET /api/v1/contract/depth/{symbol}?limit=20
  WS    sub.depth.full  -> push.depth / push.depth.full  (snapshot)
  WS    sub.depth       -> push.depth                    (incremental)

Level row: [price, quantity, order_count]
quantity == 0 on an incremental update deletes that price.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Optional, Tuple


STALE_AFTER_SEC = 5.0
BAND_PCTS = (0.10, 0.50)


def _level_row(row) -> Optional[Tuple[float, float]]:
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    try:
        px = float(row[0])
        qty = float(row[1])
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    return px, qty


class OrderBook:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.version = None
        self.exchange_ts_ms = None
        self.local_ts = None
        self.connected = False
        self._initialized = False

    def mark_disconnected(self) -> None:
        self.connected = False

    def mark_connected(self) -> None:
        self.connected = True

    def apply_snapshot(self, bids, asks, version=None, exchange_ts_ms=None, local_ts=None) -> None:
        self.bids = {}
        self.asks = {}
        self._apply_side(self.bids, bids, incremental=False)
        self._apply_side(self.asks, asks, incremental=False)
        if version is not None:
            self.version = int(version)
        self.exchange_ts_ms = exchange_ts_ms
        self.local_ts = local_ts if local_ts is not None else time.time()
        self._initialized = True
        self.connected = True

    def apply_delta(self, bids, asks, version=None, exchange_ts_ms=None, local_ts=None) -> bool:
        if not self._initialized:
            self.apply_snapshot(bids, asks, version=version, exchange_ts_ms=exchange_ts_ms, local_ts=local_ts)
            return True
        if version is not None and self.version is not None:
            v = int(version)
            if v < self.version or v > self.version + 1:
                return False
            self.version = v
        elif version is not None:
            self.version = int(version)
        self._apply_side(self.bids, bids, incremental=True)
        self._apply_side(self.asks, asks, incremental=True)
        self.exchange_ts_ms = exchange_ts_ms
        self.local_ts = local_ts if local_ts is not None else time.time()
        self.connected = True
        return True

    def _apply_side(self, side, rows, incremental: bool) -> None:
        if rows is None:
            return
        for row in rows:
            parsed = _level_row(row)
            if parsed is None:
                continue
            px, qty = parsed
            if incremental and qty <= 0:
                side.pop(px, None)
            elif qty <= 0:
                continue
            else:
                side[px] = qty

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def mid(self):
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def spread(self):
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba - bb

    def spread_pct(self):
        s, m = self.spread(), self.mid()
        if s is None or m is None or m <= 0:
            return None
        return s / m * 100.0

    def crossed(self) -> bool:
        bb, ba = self.best_bid(), self.best_ask()
        return bb is not None and ba is not None and bb >= ba

    def stale(self, now=None) -> bool:
        if self.local_ts is None:
            return True
        return ((now if now is not None else time.time()) - self.local_ts) > STALE_AFTER_SEC

    def book_valid(self) -> bool:
        return self.connected and self._initialized and bool(self.bids) and bool(self.asks) and not self.crossed() and not self.stale()

    def depth_within_pct(self, side: str, pct: float) -> float:
        m = self.mid()
        if m is None or m <= 0:
            return 0.0
        band = m * (pct / 100.0)
        if side == "bid":
            return float(sum(q for px, q in self.bids.items() if px >= m - band))
        return float(sum(q for px, q in self.asks.items() if px <= m + band))

    def imbalance(self, pct: float):
        b = self.depth_within_pct("bid", pct)
        a = self.depth_within_pct("ask", pct)
        tot = b + a
        if tot <= 0:
            return None
        return (b - a) / tot

    def snapshot_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bids": [[px, self.bids[px]] for px in sorted(self.bids, reverse=True)],
            "asks": [[px, self.asks[px]] for px in sorted(self.asks)],
            "best_bid": self.best_bid(),
            "best_ask": self.best_ask(),
            "mid": self.mid(),
            "spread": self.spread(),
            "spread_pct": self.spread_pct(),
            "version": self.version,
            "exchange_ts_ms": self.exchange_ts_ms,
            "local_ts": self.local_ts,
            "connected": self.connected,
            "book_valid": self.book_valid(),
            "stale": self.stale(),
            "crossed": self.crossed(),
        }


def parse_mexc_depth_message(msg: Dict[str, Any]):
    if not isinstance(msg, dict):
        return None
    data = msg.get("data", msg)
    if not isinstance(data, dict):
        return None
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    ts = data.get("timestamp") or data.get("ct") or msg.get("ts")
    try:
        ts_ms = int(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_ms = None
    version = data.get("version")
    try:
        version = int(version) if version is not None else None
    except (TypeError, ValueError):
        version = None
    channel = msg.get("channel") or ""
    snapshot = channel in ("push.depth.full",) or data.get("end") is True
    return {
        "bids": bids,
        "asks": asks,
        "version": version,
        "exchange_ts_ms": ts_ms,
        "snapshot": snapshot,
        "symbol": msg.get("symbol") or data.get("symbol"),
    }
