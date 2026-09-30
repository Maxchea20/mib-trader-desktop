"""Evaluation of the AI's forward paper record.  Pure accounting over stored decisions and trades - no market analysis."""
import os
import time
from collections import defaultdict
from typing import Any, Dict, List

from . import paper, store

CONF_BINS = ((0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))


def _perf(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    p = paper.performance(rows)
    return p if p.get("trades") else {"trades": 0}


def _price(name: str):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None


def usage_report(rows: List[Dict[str, Any]], now: float = None) -> Dict[str, Any]:
    """Tokens billed by OpenAI for the reviews, plus a cost estimate when USD-per-1M-token prices are set in the environment
    (SWING_AI_PRICE_IN, SWING_AI_PRICE_CACHED, SWING_AI_PRICE_OUT)."""
    now = time.time() if now is None else now
    used = [r for r in rows if r.get("input_tokens") is not None]

    def tot(rs):
        i = sum(r["input_tokens"] or 0 for r in rs)
        c = sum(r["cached_tokens"] or 0 for r in rs)
        o = sum(r["output_tokens"] or 0 for r in rs)
        return {"calls": len(rs), "input_tokens": i, "cached_tokens": c, "output_tokens": o, "cost_usd": _cost(i, c, o)}
    day0 = now // 86400 * 86400
    return {"all_time": tot(used), "today": tot([r for r in used if r["ts"] >= day0]),
            "prices_set": all(_price(k) is not None for k in ("SWING_AI_PRICE_IN", "SWING_AI_PRICE_OUT"))}


def _cost(i: int, c: int, o: int):
    pin, pout, pc = _price("SWING_AI_PRICE_IN"), _price("SWING_AI_PRICE_OUT"), _price("SWING_AI_PRICE_CACHED")
    if pin is None or pout is None:
        return None
    pc = pin if pc is None else pc
    return ((i - c) * pin + c * pc + o * pout) / 1_000_000.0


def _median(v):
    v = sorted(x for x in v if x is not None)
    return None if not v else (v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2)


def character(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Is this behaving like a swing system or a scalper?  Plain facts about the trades taken (no judgement, no market analysis)."""
    taken = [t for t in trades if t.get("fill_price") and t.get("risk_dist")]
    closed = [t for t in taken if t["status"] == "CLOSED" and t.get("closed_ts") and t.get("opened_ts")]
    held_h = [(t["closed_ts"] - t["opened_ts"]) / 3600.0 for t in closed]
    exp = [(t["expected_hold_hours"], (t["closed_ts"] - t["opened_ts"]) / 3600.0) for t in closed if t.get("expected_hold_hours")]
    return {"trades": len(taken),
            "median_stop_pct": _median([t["risk_dist"] / t["fill_price"] * 100.0 for t in taken]),
            "median_target_pct": _median([abs(t["tp"] - t["fill_price"]) / t["fill_price"] * 100.0 for t in taken if t.get("tp")]),
            "median_held_hours": _median(held_h),
            "share_closed_under_1h": (sum(1 for h in held_h if h < 1.0) / len(held_h)) if held_h else None,
            "median_expected_hold_hours": _median([t["expected_hold_hours"] for t in taken if t.get("expected_hold_hours")]),
            "median_actual_vs_expected": _median([a / e for e, a in exp if e]) if exp else None}


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
    ai_exits = [t for t in closed if t["exit_reason"] == "AI_EXIT"]
    judged = [t for t in ai_exits if t.get("cf_status") in ("DONE", "TIMEOUT") and t.get("cf_r_net") is not None]
    saved = [t["r_net"] - t["cf_r_net"] for t in judged]                     # > 0: exiting was better than holding
    ai_exit_value = {"ai_exits": len(ai_exits), "judged": len(judged), "still_running": sum(1 for t in ai_exits if t.get("cf_status") not in ("DONE", "TIMEOUT")),
                     "avg_r_saved_by_exiting": (sum(saved) / len(saved)) if saved else None,
                     "exit_was_better": sum(1 for x in saved if x > 0), "exit_was_worse": sum(1 for x in saved if x < 0)}
    all_rows = store.decisions(decision_limit)
    char = character(trades)
    return {
        "usage": usage_report(all_rows), "ai_exit_value": ai_exit_value, "character": char,
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
