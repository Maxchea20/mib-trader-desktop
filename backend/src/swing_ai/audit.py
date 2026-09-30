"""Look-ahead audit of the snapshots GPT actually received (read-only).

For each stored snapshot and timeframe it checks
  1. FUTURE   - every candle satisfies open_ts + step <= as_of (a candle still forming or in the future is a leak);
  2. REVISED  - each candle still matches the market database now.  A candle GPT saw as 'closed' that later changed was not final."""
from typing import Any, Dict, List

from ..market_data import database as db
from . import store
from .config import SwingConfig


def candle_times(blk: Dict[str, Any]) -> List[int]:
    """Rebuild every candle's open time from first_open_ts / step_seconds / time_breaks."""
    out, t = [], blk["first_open_ts"]
    breaks = {i: ts for i, ts in blk["time_breaks"]}
    for i in range(len(blk["candles"])):
        if i in breaks:
            t = breaks[i]
        out.append(t)
        t += blk["step_seconds"]
    return out


def audit_snapshot(snap: Dict[str, Any], symbol: str = "BTC_USDT", check_db: bool = True) -> Dict[str, Any]:
    as_of = snap["as_of_unix"]
    res: Dict[str, Any] = {"as_of": as_of, "future": [], "revised": [], "margin_seconds": {}, "candles": 0}
    for tf, blk in snap["timeframes"].items():
        times, step = candle_times(blk), blk["step_seconds"]
        res["candles"] += len(times)
        res["margin_seconds"][tf] = as_of - (times[-1] + step)          # >= 0 means the newest candle had closed
        res["future"] += [(tf, t) for t in times if t + step > as_of]
        if check_db:
            rows = {r["ts"]: r for r in db.get_candles(symbol, tf, limit=len(times) + 400)}
            for t, row in zip(times, blk["candles"]):
                cur = rows.get(t)
                if cur is None:
                    continue
                now_row = [round(cur["open"], 2), round(cur["high"], 2), round(cur["low"], 2), round(cur["close"], 2)]
                if any(abs(a - b) > 0.011 for a, b in zip(now_row, row[:4])):
                    res["revised"].append((tf, t))
    return res


def audit_recent(limit: int = 20, check_db: bool = True) -> Dict[str, Any]:
    ids = store._rows("SELECT id FROM swing_ai_market_snapshots ORDER BY id DESC LIMIT ?", (limit,))
    per = []
    for r in ids:
        snap = store.get_snapshot(r["id"])
        if snap:
            per.append({"id": r["id"], **audit_snapshot(snap, check_db=check_db)})
    return {"snapshots": len(per), "future_candles": sum(len(p["future"]) for p in per),
            "revised_candles": sum(len(p["revised"]) for p in per), "detail": per}
