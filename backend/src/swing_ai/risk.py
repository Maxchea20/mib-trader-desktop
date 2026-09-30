"""Hard safety / execution constraints.  These are NOT market-analysis gates: no indicator, no trend, no structure and no
volatility measure lives here, so nothing in MIB can veto the AI's market view.  The AI proposes; this code checks
geometry, limits and freshness, sizes the position, and either accepts the proposal exactly as given or rejects it."""
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import SwingConfig
from .schema import EntryDecision, ManageDecision

QTY_STEP = 0.001          # BTC
SANE_MIN_STOP_PCT, SANE_MAX_STOP_PCT, SANE_MAX_ENTRY_DISTANCE_PCT = 0.05, 25.0, 10.0     # RELAXED mode: sanity range only


@dataclass
class RiskResult:
    ok: bool
    reasons: List[str] = field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None


def _floor_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def validate_entry(dec: EntryDecision, market: Dict[str, Any], cfg: SwingConfig, ctx: Dict[str, Any]) -> RiskResult:
    """market: {price, bid, ask, spread_pct, ticker_age_seconds}.
    ctx: now, has_active, trades_today, daily_r, last_loss_ts, connected."""
    if dec.decision not in ("LONG", "SHORT"):
        return RiskResult(False, ["no trade proposed"])
    why: List[str] = []
    price = float(market["price"])
    now = ctx.get("now", time.time())
    long_ = dec.decision == "LONG"
    off = cfg.safety_mode == "OFF"                         # paper research: only refuse what cannot be simulated at all
    strict = cfg.safety_mode not in ("RELAXED", "OFF")
    min_stop, max_stop = (cfg.min_stop_pct, cfg.max_stop_pct) if strict else (SANE_MIN_STOP_PCT, SANE_MAX_STOP_PCT)
    max_dist = cfg.max_entry_distance_pct if strict else SANE_MAX_ENTRY_DISTANCE_PCT

    if not off:
        if not ctx.get("connected", True):
            why.append("market feed not connected")
        age = market.get("ticker_age_seconds")
        if age is not None and age > cfg.max_ticker_age_seconds:
            why.append(f"ticker stale ({age}s)")
        sp = market.get("spread_pct")
        if sp is None:
            why.append("spread unknown")
        elif sp > cfg.max_spread_pct:
            why.append(f"spread {sp}% above {cfg.max_spread_pct}%")
    if not off and dec.confidence < cfg.min_confidence:
        why.append(f"confidence {dec.confidence} below {cfg.min_confidence}")
    if ctx.get("has_active"):
        why.append("a position or pending order already exists")
    if strict:                                          # discretionary limits: only in STRICT mode
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

    dist_pct = abs(entry - price) / price * 100.0
    near_market = dist_pct <= cfg.market_tolerance_pct
    passive = (long_ and entry <= price) or (not long_ and entry >= price)
    etype = "MARKET" if near_market else ("LIMIT" if passive else "STOP")      # the AI's own price decides how the order behaves
    if not off:
        if dec.entry_type == "MARKET" and not near_market:
            why.append("entry is not at the market price for a MARKET order")
        if not near_market:
            if dist_pct > max_dist:
                why.append("limit entry too far from price")
            if not passive:
                why.append("limit entry is on the wrong side of price (it would be a stop entry)")
    fill = price if near_market else entry
    if near_market and market.get("bid") and market.get("ask"):
        fill = float(market["ask"]) if long_ else float(market["bid"])
        if not off and ((long_ and not (sl < fill < tp)) or (not long_ and not (tp < fill < sl))):
            why.append("SL/TP no longer valid at the current bid/ask")

    risk_dist, reward = abs(fill - sl), abs(tp - fill)
    if risk_dist <= 0:
        why.append("zero stop distance")
    else:
        stop_pct = risk_dist / fill * 100.0
        if not off and stop_pct < min_stop:
            why.append(f"stop {stop_pct:.2f}% is tighter than {min_stop}%")
        if not off and stop_pct > max_stop:
            why.append(f"stop {stop_pct:.2f}% is wider than {max_stop}%")
        if strict and reward / risk_dist < cfg.min_rr:
            why.append(f"reward/risk {reward / risk_dist:.2f} below {cfg.min_rr}")
    if why:
        return RiskResult(False, why)

    risk_usd = cfg.equity_usd * cfg.risk_pct / 100.0
    qty = risk_usd / risk_dist
    qty = min(qty, cfg.max_position_usd / fill, cfg.equity_usd * cfg.max_leverage / fill)
    qty = round(_floor_step(qty, QTY_STEP), 3)
    if qty < QTY_STEP:
        return RiskResult(False, ["position would be smaller than the minimum order size"])
    plan = {
        "side": dec.decision, "entry_type": etype, "entry": fill, "sl": sl, "tp": tp,
        "qty": qty, "notional": qty * fill, "risk_usd": qty * risk_dist, "risk_dist": risk_dist,
        "leverage": qty * fill / cfg.equity_usd, "rr": reward / risk_dist,
        "fee_entry": cfg.maker_fee if etype == "LIMIT" else cfg.taker_fee, "fee_tp": cfg.maker_fee, "fee_sl": cfg.taker_fee,
        "expires_ts": None if etype == "MARKET" else int(now + cfg.limit_expiry_minutes * 60),
        "invalidation_price": dec.invalidation_price, "sized_down": qty * risk_dist < risk_usd * 0.98,           # a real reduction (limits), not step rounding
    }
    return RiskResult(True, [], plan)


def validate_manage(md: ManageDecision, position: Dict[str, Any], price: float, cfg: SwingConfig) -> RiskResult:
    """HOLD / EXIT always allowed.  MOVE_SL may only reduce risk and must stay on the protective side of price."""
    if md.action != "MOVE_SL":
        return RiskResult(True, [], {"action": md.action})
    long_ = position["side"] == "LONG"
    new, cur = md.new_sl, position["sl"]
    buf = price * cfg.market_tolerance_pct / 100.0
    why = []
    if new is None:
        return RiskResult(False, ["no new_sl"])
    if cfg.safety_mode == "OFF":                       # paper research: any stop on the protective side of price
        if (long_ and not new < price) or (not long_ and not new > price):
            why.append("new stop must be on the protective side of price")
    else:
        if long_ and not (cur < new < price - buf):
            why.append("new stop must be above the current stop and below price")
        if not long_ and not (price + buf < new < cur):
            why.append("new stop must be below the current stop and above price")
    return RiskResult(not why, why, {"action": "MOVE_SL", "new_sl": new})
