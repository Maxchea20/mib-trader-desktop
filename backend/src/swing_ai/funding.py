"""Perpetual funding for paper trades.  MEXC settles funding every 8 hours (00:00, 08:00, 16:00 UTC): a position open at that moment pays
(or receives) notional x rate.  With a positive rate longs pay shorts, with a negative rate shorts pay longs.  A swing trade held for days
crosses many settlements, so this belongs in net R.  Position value is approximated at the entry price (MEXC uses the mark price)."""
import logging
import time
from typing import Dict, List

logger = logging.getLogger(__name__)
SETTLE_HOURS_UTC = (0, 8, 16)
DEFAULT_RATE = 0.000049            # +0.0049% per 8h (about 5.4% APR), the rate shown on MEXC when this was set; override with SWING_AI config
USE_HISTORY = False                # the running app turns this on; tests never touch the network
_history: Dict[str, object] = {"at": 0.0, "rates": {}}


def settlements(t0: float, t1: float) -> List[int]:
    """Settlement timestamps in (t0, t1]."""
    if t1 <= t0:
        return []
    out, day = [], int(t0 // 86400) * 86400 - 86400
    while day <= t1:
        for h in SETTLE_HOURS_UTC:
            t = day + h * 3600
            if t0 < t <= t1:
                out.append(t)
        day += 86400
    return sorted(out)


def _refresh_history() -> None:
    if time.time() - float(_history["at"]) < 3600:
        return
    _history["at"] = time.time()
    try:
        from ..market_data import mexc_market_data as mkt
        res = mkt.rest_get("/api/v1/contract/funding_rate/history?symbol=BTC_USDT&page_num=1&page_size=100")
        rows = ((res.get("data") or {}).get("resultList") or []) if res.get("success") else []
        _history["rates"] = {int(r["settleTime"]) // 1000: float(r["fundingRate"]) for r in rows if r.get("settleTime") is not None}
    except Exception:
        logger.warning("funding history unavailable, using the default rate", exc_info=False)


def rate_at(ts: int) -> float:
    if USE_HISTORY:
        _refresh_history()
        rates = _history["rates"]
        if rates:
            near = min(rates, key=lambda k: abs(k - ts))
            if abs(near - ts) <= 3600:
                return rates[near]
    return DEFAULT_RATE


def funding_usd(side: str, qty: float, price: float, t0: float, t1: float) -> float:
    """Positive = the trade PAID funding, negative = it RECEIVED funding."""
    sign = 1.0 if side == "LONG" else -1.0
    return sum(sign * qty * price * rate_at(t) for t in settlements(t0, t1))
