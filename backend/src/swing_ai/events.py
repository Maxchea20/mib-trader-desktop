"""Deterministic market events that wake the AI.  Pure functions of two consecutive snapshot summaries.

The AI is never called on ticks; it is called on the heartbeat and when one of these fires.  Each event is
edge-triggered (it fires when a condition becomes true, not while it stays true) and has a per-kind cooldown."""
from typing import Any, Dict, List, Optional


def summarize(snap: Dict[str, Any]) -> Dict[str, Any]:
    """The few facts event detection needs, small enough to keep between calls."""
    tf = snap["timeframes"]
    def last_ev(t):
        ev = tf[t]["structure_events"]
        return (ev[-1]["ts"], ev[-1]["type"], ev[-1]["direction"]) if ev else None
    sw = tf["15m"].get("liquidity_sweeps") or []
    return {
        "as_of": snap["as_of"], "price": snap["market"]["price"],
        "trend": {t: tf[t]["trend"] for t in ("4h", "1h", "15m")},
        "last_structure": {t: last_ev(t) for t in ("4h", "1h", "15m")},
        "last_sweep_ts": sw[-1]["ts"] if sw else None,
        "atr15": tf["15m"]["atr"], "atr_ratio_15m": tf["15m"]["volatility"]["atr_ratio_vs_median100"],
        "last15_ts": tf["15m"]["last_closed_ts"],
        "last15_close": tf["15m"]["candles"][-1][4], "prev15_close": tf["15m"]["candles"][-2][4],
        "last15_range_atr": tf["15m"]["momentum"]["last_range_atr"],
        "rsi_1h": tf["1h"]["momentum"]["rsi14"], "rsi_15m": tf["15m"]["momentum"]["rsi14"],
        "levels": [(lv["price"], lv["touches"]) for t in ("4h", "1h")
                   for lv in tf[t]["levels"]["resistance_above"] + tf[t]["levels"]["support_below"] if lv["touches"] >= 2],
    }


def detect(prev: Optional[Dict[str, Any]], cur: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Events between the previous summary and the current one (empty on the first call)."""
    out: List[Dict[str, Any]] = []
    if prev is None:
        return out
    for t in ("4h", "1h"):
        if cur["last_structure"][t] != prev["last_structure"][t] and cur["last_structure"][t]:
            ts, typ, d = cur["last_structure"][t]
            out.append({"kind": f"STRUCTURE_{t.upper()}", "tf": t, "detail": f"{typ} {d}", "key": f"{t}:{ts}"})
        elif cur["trend"][t] != prev["trend"][t]:
            out.append({"kind": f"STRUCTURE_{t.upper()}", "tf": t, "detail": f"trend {prev['trend'][t]}->{cur['trend'][t]}",
                        "key": f"{t}:trend:{cur['trend'][t]}"})
    if cur["last_structure"]["15m"] != prev["last_structure"]["15m"] and cur["last_structure"]["15m"]:
        ts, typ, d = cur["last_structure"]["15m"]
        out.append({"kind": "STRUCTURE_15M", "tf": "15m", "detail": f"{typ} {d}", "key": f"15m:{ts}"})
    if cur["last_sweep_ts"] and cur["last_sweep_ts"] != prev["last_sweep_ts"]:
        out.append({"kind": "LIQUIDITY_SWEEP_15M", "tf": "15m", "detail": "liquidity sweep", "key": f"sw:{cur['last_sweep_ts']}"})
    a15 = cur["atr15"] or 0.0
    if a15 and cur["last15_ts"] != prev["last15_ts"]:            # closed-15m-bar events, once per bar
        for price, touches in cur["levels"]:
            a, b = prev["prev15_close"], cur["last15_close"]
            if (a < price <= b) or (a > price >= b):
                out.append({"kind": "LEVEL_BREAK_OR_RECLAIM", "tf": "15m",
                            "detail": f"15m closed {'above' if b > price else 'below'} level {price} ({touches} touches)",
                            "key": f"lvl:{round(price)}:{cur['last15_ts']}"})
                break
        ra, rb = prev["atr_ratio_15m"] or 0, cur["atr_ratio_15m"] or 0
        if (rb >= 1.6 and ra < 1.6) or (cur["last15_range_atr"] or 0) >= 2.5:
            out.append({"kind": "VOLATILITY_EXPANSION", "tf": "15m",
                        "detail": f"atr ratio {rb}, last range {cur['last15_range_atr']} ATR", "key": f"vol:{cur['last15_ts']}"})
    if prev["rsi_1h"] is not None and cur["rsi_1h"] is not None and (prev["rsi_1h"] - 50) * (cur["rsi_1h"] - 50) < 0:
        out.append({"kind": "MOMENTUM_SHIFT", "tf": "1h", "detail": f"1h RSI crossed 50 ({prev['rsi_1h']}->{cur['rsi_1h']})",
                    "key": f"rsi1h:{int(cur['rsi_1h'] > 50)}:{cur['as_of'] // 3600}"})
    if cur["price"] and a15:                                       # price proximity to a multi-touch level (edge-triggered)
        for price, touches in cur["levels"]:
            near = abs(cur["price"] - price) <= 0.25 * a15
            was = abs(prev["price"] - price) <= 0.25 * a15
            if near and not was:
                out.append({"kind": "LEVEL_INTERACTION", "tf": "15m", "detail": f"price at level {price} ({touches} touches)",
                            "key": f"near:{round(price)}"})
                break
    if position and position.get("status") == "OPEN":
        out += invalidation_events(position, cur["price"], a15)
    return out


def invalidation_events(position: Dict[str, Any], price: Optional[float], atr15: float) -> List[Dict[str, Any]]:
    """Checked against the live price on every tick (cheap, no snapshot needed)."""
    out = []
    if price is None:
        return out
    long_ = position["side"] == "LONG"
    inv = position.get("invalidation_price")
    if inv is not None and ((long_ and price <= inv) or (not long_ and price >= inv)):
        out.append({"kind": "TRADE_INVALIDATION", "tf": "live", "detail": f"price {price} through invalidation {inv}",
                    "key": f"inv:{position['id']}", "urgent": True})
    sl = position.get("sl")
    if sl is not None and atr15 and abs(price - sl) <= 0.25 * atr15 and ((long_ and price > sl) or (not long_ and price < sl)):
        out.append({"kind": "STOP_PROXIMITY", "tf": "live", "detail": f"price within 0.25 ATR of stop {sl}",
                    "key": f"sl:{position['id']}", "urgent": True})
    return out


class Cooldown:
    """Per (kind, key) de-duplication plus a per-kind quiet period."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        self.seen: Dict[str, float] = {}
        self.kind_last: Dict[str, float] = {}

    def allow(self, ev: Dict[str, Any], now: float) -> bool:
        key = f"{ev['kind']}|{ev.get('key')}"
        if key in self.seen:
            return False
        if now - self.kind_last.get(ev["kind"], -1e18) < self.seconds and not ev.get("urgent"):
            return False
        self.seen[key] = now
        self.kind_last[ev["kind"]] = now
        return True
