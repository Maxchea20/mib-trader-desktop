"""When to wake the AI.  These are RAW mechanical conditions - clock and arithmetic on price - never a market opinion.

  * candle-close clock ticks (1H / 4H / 1D bar just closed)
  * a raw percentage price move over a short window (cost control, not an interpretation)
  * price crossing a level THE AI ITSELF asked to be woken at (its own alert)
  * open trade: price through the invalidation price the AI itself set, or within a fixed % of the stop (safety)

Nothing here is sent to the AI as analysis; the AI only receives the wake reason as a label."""
from collections import deque
from typing import Any, Dict, List, Optional

CLOCK_TFS = ("1h", "4h", "1d")


class WakeWatcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_close_ts: Dict[str, Optional[int]] = {}
        self.prices = deque()                        # (unix, price) samples for the raw move gate
        self.prev_price: Optional[float] = None
        self.ai_levels: List[Dict[str, Any]] = []    # the AI's own alerts: {"price","direction","reason"}
        self.seen: Dict[str, float] = {}
        self.kind_last: Dict[str, float] = {}

    def set_ai_levels(self, levels: Optional[List[Dict[str, Any]]]) -> None:
        self.ai_levels = [dict(l) for l in (levels or [])]

    def _allow(self, w: Dict[str, Any], now: float) -> bool:
        key = f"{w['kind']}|{w.get('key')}"
        if key in self.seen:
            return False
        if not w.get("urgent") and now - self.kind_last.get(w["kind"], -1e18) < self.cfg.event_cooldown_seconds:
            return False
        self.seen[key] = now
        self.kind_last[w["kind"]] = now
        return True

    def check(self, now: float, price: float, source, position: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        first = not self.last_close_ts
        for tf in CLOCK_TFS:
            rows = source.closed(tf, 1, now)
            ts = int(rows[-1]["ts"]) if rows else None
            if not first and ts is not None and ts != self.last_close_ts.get(tf):
                out.append({"kind": f"CANDLE_CLOSE_{tf.upper()}", "detail": f"a new {tf.upper()} candle closed", "key": f"{tf}:{ts}"})
            self.last_close_ts[tf] = ts
        self.prices.append((now, price))
        while self.prices and now - self.prices[0][0] > self.cfg.price_move_window_seconds:
            self.prices.popleft()
        base = self.prices[0][1]
        move = (price / base - 1.0) * 100.0 if base else 0.0
        if abs(move) >= self.cfg.price_move_trigger_pct and now - self.prices[0][0] >= 60:
            out.append({"kind": "PRICE_MOVE", "detail": f"price moved {move:+.2f}% in {int((now - self.prices[0][0]) / 60)} min",
                        "key": f"mv:{int(now // 900)}:{1 if move > 0 else -1}"})
        if self.prev_price is not None:
            for lv in list(self.ai_levels):
                p = float(lv["price"])
                up = self.prev_price < p <= price
                dn = self.prev_price > p >= price
                if (lv.get("direction") == "ABOVE" and up) or (lv.get("direction") == "BELOW" and dn):
                    out.append({"kind": "AI_WAKE_LEVEL", "detail": f"price crossed {p} ({lv.get('direction')}) - your alert: {lv.get('reason', '')}",
                                "key": f"lv:{p}:{lv.get('direction')}", "urgent": True})
                    self.ai_levels.remove(lv)              # one-shot
        self.prev_price = price
        if position and position.get("status") == "OPEN":
            out += self.position_wakes(position, price)
        return [w for w in out if self._allow(w, now)]

    def position_wakes(self, position: Dict[str, Any], price: float) -> List[Dict[str, Any]]:
        w: List[Dict[str, Any]] = []
        tid = position.get("trade_id", position.get("id"))
        long_ = position["side"] == "LONG"
        inv = position.get("your_invalidation_price", position.get("invalidation_price"))
        if inv is not None and ((long_ and price <= inv) or (not long_ and price >= inv)):
            w.append({"kind": "INVALIDATION_LEVEL_HIT", "detail": f"price {price} reached your invalidation price {inv}",
                      "key": f"inv:{tid}", "urgent": True})
        sl = position.get("stop_loss")
        if sl and abs(price - sl) / price * 100 <= self.cfg.stop_proximity_pct and ((long_ and price > sl) or (not long_ and price < sl)):
            w.append({"kind": "STOP_NEAR", "detail": f"price {price} is within {self.cfg.stop_proximity_pct}% of the stop {sl}",
                      "key": f"sl:{tid}", "urgent": True})
        return w
