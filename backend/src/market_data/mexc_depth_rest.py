"""REST snapshot of MEXC futures depth. Live only."""
from typing import Dict, Optional
from .mexc_market_data import rest_get


def get_depth(symbol: str, limit: int = 20) -> Optional[Dict]:
    data = rest_get(f"/api/v1/contract/depth/{symbol}?limit={int(limit)}")
    if not data.get("success"):
        return None
    d = data.get("data") or {}
    return {
        "bids": d.get("bids") or [],
        "asks": d.get("asks") or [],
        "version": d.get("version"),
        "timestamp": d.get("timestamp") or d.get("ts"),
        "symbol": symbol,
    }
