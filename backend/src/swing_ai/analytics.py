"""Evaluation of the AI's forward paper record.  Pure accounting over stored decisions and trades - no market analysis."""
from collections import defaultdict
from typing import Any, Dict, List

from . import paper, store

CONF_BINS = ((0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))


def _perf(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    p = paper.performance(rows)
    return p if p.get("trades") else {"trades": 0}


def report(decision_limit: int = 100000) -> Dict[str, Any]:
    dec = [d for d in store.decisions(decision_limit) if d["kind"] == "ENTRY"]
    trades = store.trades()
    closed = [t for t in trades if t["status"] == "CLOSED" and t["r_net"] is not None]
    n = len(dec)
    counts = defaultdict(int)
    for d in dec:
        counts[d["decision"]] += 1
    proposals = counts["LONG"] + counts["SHORT"]
    span_days = ((dec[0]["ts"] - dec[-1]["ts"]) / 86400.0) if len(dec) > 1 else 0.0
    by_side = {s: _perf([t for t in closed if t["side"] == s]) for s in ("LONG", "SHORT")}
    states = sorted({t["market_state"] or "UNKNOWN" for t in closed})
    by_state = {s: _perf([t for t in closed if (t["market_state"] or "UNKNOWN") == s]) for s in states}
    calib = []
    for lo, hi in CONF_BINS:
        g = [t for t in closed if t["confidence"] is not None and lo <= t["confidence"] < hi]
        if g:
            calib.append({"confidence_bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "trades": len(g),
                          "mean_confidence": sum(t["confidence"] for t in g) / len(g),
                          "win_rate": sum(1 for t in g if t["r_net"] > 0) / len(g),
                          "mean_net_r": sum(t["r_net"] for t in g) / len(g)})
    return {
        "frequency": {"entry_reviews": n, "LONG": counts["LONG"], "SHORT": counts["SHORT"], "NO_TRADE": counts["NO_TRADE"],
                      "no_trade_share": (counts["NO_TRADE"] / n) if n else None,
                      "proposals_rejected_by_safety": sum(1 for d in dec if d["decision"] in ("LONG", "SHORT") and not d["risk_ok"]),
                      "ai_errors": sum(1 for d in store.decisions(decision_limit) if d["error"]),
                      "reviews_per_day": (n / span_days) if span_days >= 1 else None,          # only meaningful after a day of data
                      "proposals_per_day": (proposals / span_days) if span_days >= 1 else None},
        "overall": _perf(closed), "by_side": by_side, "by_market_state": by_state, "confidence_calibration": calib,
        "trades": {"total": len(trades), "closed": len(closed),
                   "active": sum(1 for t in trades if t["status"] in ("OPEN", "PENDING")),
                   "never_filled": sum(1 for t in trades if t["status"] in ("CANCELLED", "EXPIRED"))},
    }
