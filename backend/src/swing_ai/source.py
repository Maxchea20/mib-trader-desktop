"""Candle sources.  Both enforce the same rule: a candle is usable only when ts + tf_seconds <= now."""
from typing import Dict, List, Optional, Sequence

TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def closed_only(rows: Sequence[dict], tf: str, now: float) -> List[dict]:
    step = TF_SEC[tf]
    return [r for r in rows if int(r["ts"]) + step <= now]


class DbSource:
    """Live source: the app's SQLite candle table through its closed-candle reader."""

    def closed(self, tf: str, limit: int, now: float) -> List[dict]:
        from ..market_data import data_access as dao
        return closed_only(dao.read_closed_candles(tf, limit=limit, now=now), tf, now)


class ListSource:
    """In-memory source for tests and replays; never returns anything not closed at `now`."""

    def __init__(self, data: Dict[str, Sequence[dict]]):
        self.data = data

    def closed(self, tf: str, limit: int, now: float) -> List[dict]:
        return closed_only(self.data.get(tf, []), tf, now)[-limit:]
