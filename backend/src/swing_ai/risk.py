"""Deterministic risk / validation layer.  The AI proposes; this code accepts or rejects and sizes.  It never
edits an AI level - a proposal is either valid exactly as given or it is rejected with reasons."""
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import SwingConfig
from .schema import EntryDecision, ManageDecision

QTY_STEP = 0.001          # BTC


@dataclass
class RiskResult:
    ok: bool
    reasons: List[str] = field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None


def _floor_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def validate_entry(dec: EntryDecision, snap: Dict[str, Any], cfg: SwingConfig, ctx: Dict[str, Any]) -> RiskResult:
    """ctx: now, has_active (open or pending position), trades_today, daily_r, last_loss_ts, connected."""
    why: List[str] = []
    if dec.decision not in ("LONG", "SHORT"):
        return RiskResult(False, ["no trade proposed"])
    m = snap["market"]
    price = float(m["price"])
    a15 = snap["timeframes"]["15m"]["atr"]
    a1h = snap["timeframes"]["1h"]["atr"]
    now = ctx.get("now", time.time())
    long_ = dec.decision == "LONG"

    if not ctx.get("connected", True):
        why.append("market feed not connected")
    age = m.get("ticker_age_seconds")
    if age is not None and age > cfg.max_ticker_age_seconds:
        why.append(f"ticker stale ({age}s)")
    sp = m.get("spread_pct")
    if sp is None:
        why.append("spread unknown")
    elif sp > cfg.max_spread_pct:
        why.append(f"spread {sp}% above {cfg.max_spread_pct}%")
    if dec.confidence < cfg.min_confidence:
        why.append(f"confidence {dec.confidence} below {cfg.min_confidence}")
    if ctx.get("has_active"):
        why.append("a position or pending order already exists")
    if ctx.get("trades_today", 0) >= cfg.max_trades_per_day:
        why.append("daily trade limit reached")
    if ctx.get("daily_r", 0.0) <= -cfg.max_daily_loss_r:
        why.append("daily loss limit reached")
    ll = ctx.get("last_loss_ts")
    if ll and now - ll < cfg.cooldown_after_loss_minutes * 60:
        why.append("cooling down after a loss")

    entry, sl, tp = dec.entry, dec.sl, dec.tp
    if entry is None or sl is None or tp is None:
        return RiskResult(False, why + ["missing entry/sl/tp"])
    for nm, v in (("entry", entry), ("sl", sl), ("tp", tp)):
        if v <= 0 or v != v:
            why.append(f"{nm} invalid")
    if long_ and not (sl < entry < tp):
        why.append("LONG needs sl < entry < tp")
    if not long_ and not (tp < entry < sl):
        why.append("SHORT needs tp < entry < sl")

    near_market = abs(entry - price) <= cfg.market_tolerance_atr15 * a15
    if dec.entry_type == "MARKET" and not near_market:
        why.append("entry is not at the market price for a MARKET order")
    if not near_market:
        if abs(entry - price) > cfg.max_entry_distance_atr1h * a1h:
            why.append("limit entry too far from price")
        if (long_ and entry > price) or (not long_ and entry < price):
            why.append("limit entry is on the wrong side of price (it would be a stop entry)")
    fill = price if near_market else entry
    if near_market and m.get("bid") and m.get("ask"):
        fill = float(m["ask"]) if long_ else float(m["bid"])
        if long_ and not (sl < fill < tp) or (not long_ and not (tp < fill < sl)):
            why.append("SL/TP no longer valid at the current bid/ask")

    risk_dist, reward = abs(fill - sl), abs(tp - fill)
    if risk_dist <= 0:
        why.append("zero stop distance")
    else:
        if risk_dist < cfg.min_stop_atr1h * a1h:
            why.append(f"stop {risk_dist / a1h:.2f} ATR(1h) is tighter than {cfg.min_stop_atr1h}")
        if risk_dist > cfg.max_stop_atr1h * a1h:
            why.append(f"stop {risk_dist / a1h:.2f} ATR(1h) is wider than {cfg.max_stop_atr1h}")
        if reward / risk_dist < cfg.min_rr:
            why.append(f"reward/risk {reward / risk_dist:.2f} below {cfg.min_rr}")
    if why:
        return RiskResult(False, why)

    risk_usd = cfg.equity_usd * cfg.risk_pct / 100.0
    qty = risk_usd / risk_dist
    qty = min(qty, cfg.max_position_usd / fill, cfg.equity_usd * cfg.max_leverage / fill)
    qty = _floor_step(qty, QTY_STEP)
    if qty < QTY_STEP:
        return RiskResult(False, ["position would be smaller than the minimum order size"])
    entry_fee = cfg.taker_fee if near_market else cfg.maker_fee
    plan = {
        "side": dec.decision, "entry_type": "MARKET" if near_market else "LIMIT", "entry": fill, "sl": sl, "tp": tp,
        "qty": qty, "notional": qty * fill, "risk_usd": qty * risk_dist, "risk_dist": risk_dist,
        "leverage": qty * fill / cfg.equity_usd, "rr": reward / risk_dist,
        "fee_entry": entry_fee, "fee_tp": cfg.maker_fee, "fee_sl": cfg.taker_fee,
        "expires_ts": None if near_market else int(now + cfg.limit_expiry_minutes * 60),
        "invalidation_price": dec.invalidation_price, "sized_down": qty * risk_dist < risk_usd * 0.999,
    }
    return RiskResult(True, [], plan)


def validate_manage(md: ManageDecision, position: Dict[str, Any], price: float, atr15: float,
                    cfg: SwingConfig) -> RiskResult:
    """HOLD / EXIT always allowed.  MOVE_SL may only reduce risk and must stay on the protective side of price."""
    if md.action != "MOVE_SL":
        return RiskResult(True, [], {"action": md.action})
    long_ = position["side"] == "LONG"
    new, cur = md.new_sl, position["sl"]
    why = []
    if new is None:
        return RiskResult(False, ["no new_sl"])
    if long_ and not (cur < new < price - 0.1 * atr15):
        why.append("new stop must be above the current stop and below price")
    if not long_ and not (price + 0.1 * atr15 < new < cur):
        why.append("new stop must be below the current stop and above price")
    return RiskResult(not why, why, {"action": "MOVE_SL", "new_sl": new})
