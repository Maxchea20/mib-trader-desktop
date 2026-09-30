"""Paper broker.  Simulates fills causally: pending limit orders fill only when a later closed 1m bar (or the live
price) trades through the level; on the fill bar only the stop is checked (conservative); SL wins ties."""
import json
from typing import Any, Dict, List, Optional

from . import store
from .config import SwingConfig


def _dir(p: Dict[str, Any]) -> int:
    return 1 if p["side"] == "LONG" else -1


def open_from_plan(plan: Dict[str, Any], decision: Dict[str, Any], decision_id: int, now: float,
                   bid: Optional[float], ask: Optional[float]) -> int:
    market = plan["entry_type"] == "MARKET"
    fill = plan["entry"] if not market else (ask if plan["side"] == "LONG" else bid) or plan["entry"]
    row = {
        "status": "OPEN" if market else "PENDING", "side": plan["side"], "entry_type": plan["entry_type"],
        "plan_entry": plan["entry"], "fill_price": fill if market else None, "sl": plan["sl"], "sl0": plan["sl"],
        "tp": plan["tp"], "qty": plan["qty"], "risk_usd": plan["risk_usd"], "risk_dist": abs(fill - plan["sl"]) if market else plan["risk_dist"],
        "created_ts": int(now), "opened_ts": int(now) if market else None, "expires_ts": plan["expires_ts"],
        "thesis": decision.get("thesis"), "invalidation": decision.get("invalidation"),
        "invalidation_price": plan.get("invalidation_price"), "decision_id": decision_id,
        "fee_entry": plan["fee_entry"], "fee_tp": plan["fee_tp"], "fee_sl": plan["fee_sl"],
        "last_bar_ts": int(now // 60 * 60), "meta": json.dumps({"rr": plan["rr"], "leverage": plan["leverage"], "sized_down": plan["sized_down"]}),
    }
    pid = store.add_position(row)
    store.set_decision_position(decision_id, pid)
    return pid


def _close(p: Dict[str, Any], price: float, reason: str, now: float) -> Dict[str, Any]:
    d = _dir(p)
    rd = p["risk_dist"]
    gross_r = d * (price - p["fill_price"]) / rd
    fee_exit = p["fee_tp"] if reason == "TP" else p["fee_sl"]
    fees_usd = p["qty"] * (p["fill_price"] * p["fee_entry"] + price * fee_exit)
    net_r = gross_r - fees_usd / (p["qty"] * rd)
    store.update_position(p["id"], status="CLOSED", exit_price=price, exit_reason=reason, closed_ts=int(now),
                          r_gross=gross_r, r_net=net_r, fees_usd=fees_usd)
    return {**p, "status": "CLOSED", "exit_price": price, "exit_reason": reason, "r_gross": gross_r, "r_net": net_r}


def close_now(p: Dict[str, Any], price: float, reason: str, now: float) -> Dict[str, Any]:
    """Discretionary / emergency exit at the given market price (taker)."""
    return _close(p, price, reason, now)


def cancel(p: Dict[str, Any], reason: str, now: float) -> None:
    store.update_position(p["id"], status="CANCELLED", exit_reason=reason, closed_ts=int(now))


def _excursions(p: Dict[str, Any], hi: float, lo: float) -> Dict[str, float]:
    d, rd, f = _dir(p), p["risk_dist"], p["fill_price"]
    fav = (hi - f) / rd if d > 0 else (f - lo) / rd
    adv = (f - lo) / rd if d > 0 else (hi - f) / rd
    return {"mfe_r": max(p["mfe_r"] or 0.0, fav), "mae_r": max(p["mae_r"] or 0.0, adv)}


def process_bar(p: Dict[str, Any], bar: Dict[str, Any], cfg: SwingConfig) -> Dict[str, Any]:
    """Advance one position through one CLOSED 1m bar.  Returns the updated position dict."""
    ts_close = int(bar["ts"]) + 60
    hi, lo = float(bar["high"]), float(bar["low"])
    if p["status"] == "PENDING":
        if p["expires_ts"] and ts_close > p["expires_ts"]:
            cancel(p, "EXPIRED", ts_close)
            return {**p, "status": "EXPIRED"}
        lim = p["plan_entry"]
        filled = (lo <= lim) if p["side"] == "LONG" else (hi >= lim)
        if not filled:
            return p
        p = {**p, "status": "OPEN", "fill_price": lim, "opened_ts": ts_close,
             "risk_dist": abs(lim - p["sl0"])}
        store.update_position(p["id"], status="OPEN", fill_price=lim, opened_ts=ts_close, risk_dist=p["risk_dist"])
        # fill bar: only the stop counts (conservative); target must be reached on a later bar
        if (p["side"] == "LONG" and lo <= p["sl"]) or (p["side"] == "SHORT" and hi >= p["sl"]):
            return _close(p, p["sl"], "SL", ts_close)
        ex = _excursions(p, hi, lo)
        store.update_position(p["id"], **ex)
        return {**p, **ex}
    if p["status"] != "OPEN":
        return p
    long_ = p["side"] == "LONG"
    hit_sl = lo <= p["sl"] if long_ else hi >= p["sl"]
    hit_tp = hi >= p["tp"] if long_ else lo <= p["tp"]
    ex = _excursions(p, hi, lo)
    store.update_position(p["id"], **ex, last_bar_ts=int(bar["ts"]))
    p = {**p, **ex}
    if hit_sl:                                         # SL wins a same-bar tie
        return _close(p, p["sl"], "SL" if abs(p["sl"] - p["sl0"]) < 1e-9 else "SL_MOVED", ts_close)
    if hit_tp:
        return _close(p, p["tp"], "TP", ts_close)
    return p


def process_price(p: Dict[str, Any], price: float, now: float) -> Dict[str, Any]:
    """Live-tick check between bar closes: fills a pending limit, or hits SL/TP, at the level (no slippage)."""
    if p["status"] == "PENDING":
        return p                                       # fills are decided only on closed bars (causal, conservative)
    if p["status"] != "OPEN":
        return p
    long_ = p["side"] == "LONG"
    if (long_ and price <= p["sl"]) or (not long_ and price >= p["sl"]):
        return _close(p, p["sl"], "SL" if abs(p["sl"] - p["sl0"]) < 1e-9 else "SL_MOVED", now)
    if (long_ and price >= p["tp"]) or (not long_ and price <= p["tp"]):
        return _close(p, p["tp"], "TP", now)
    ex = _excursions(p, price, price)
    if ex["mfe_r"] > (p["mfe_r"] or 0) or ex["mae_r"] > (p["mae_r"] or 0):
        store.update_position(p["id"], **ex)
        p = {**p, **ex}
    return p


def performance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [r for r in rows if r["status"] == "CLOSED" and r["r_net"] is not None]
    n = len(closed)
    if not n:
        return {"trades": 0}
    r = [x["r_net"] for x in closed]
    g = [x["r_gross"] for x in closed]
    mean = sum(r) / n
    sd = (sum((x - mean) ** 2 for x in r) / (n - 1)) ** 0.5 if n > 1 else 0.0
    wins = sum(x for x in r if x > 0)
    losses = -sum(x for x in r if x <= 0)
    return {"trades": n, "win_rate": sum(1 for x in r if x > 0) / n, "net_r": sum(r), "gross_r": sum(g),
            "expectancy_r": mean, "t_stat": (mean / (sd / n ** 0.5)) if sd > 0 else 0.0,
            "profit_factor": (wins / losses) if losses > 0 else float("inf"),
            "avg_mfe_r": sum(x["mfe_r"] or 0 for x in closed) / n, "avg_mae_r": sum(x["mae_r"] or 0 for x in closed) / n,
            "fees_usd": sum(x["fees_usd"] or 0 for x in closed),
            "exits": {k: sum(1 for x in closed if x["exit_reason"] == k) for k in {x["exit_reason"] for x in closed}}}
