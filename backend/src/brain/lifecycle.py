"""Post-FIRE Brain: one engine owns HOLD / TRAIL / EXIT.

No votes. No separate trade manager.
First slice of the recovered Brain lifecycle spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT

LIFECYCLE_VERSION = "BRAIN_LIFECYCLE_V1"
HOLD, TRAIL, EXIT = "HOLD", "TRAIL", "EXIT"


@dataclass
class Thesis:
    side: str
    event: Optional[str]
    level: Optional[float]
    reasons: List[str] = field(default_factory=list)
    story_tf: str = "15m"
    valid: bool = True
    invalid_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    trade_id: str
    side: str
    entry: float
    sl: float
    tp: Optional[float]
    qty: float
    risk_usd: float
    equity_at_entry: float
    atr: float
    thesis: Thesis
    opened_ts: Optional[int] = None
    trail_sl: Optional[float] = None
    best: Optional[float] = None
    r_now: float = 0.0
    protected: bool = False

    def hard_sl(self) -> float:
        t = self.trail_sl
        if t is None:
            return self.sl
        if self.side == LONG:
            return max(self.sl, t)
        return min(self.sl, t)


def size_from_risk(equity: float, risk_pct: float, entry: float, sl: float) -> Dict[str, float]:
    """Capital → risk → SL distance → qty. Never size first."""
    risk_usd = max(0.0, float(equity) * float(risk_pct))
    stop = abs(float(entry) - float(sl))
    stop_frac = stop / float(entry) if entry else 0.0
    qty = (risk_usd / stop) if stop > 0 else 0.0
    notional = qty * float(entry)
    return {
        "risk_usd": risk_usd,
        "stop_distance": stop,
        "stop_frac": stop_frac,
        "qty": qty,
        "notional": notional,
    }


def thesis_from_fire(fire: Dict[str, Any]) -> Thesis:
    return Thesis(
        side=fire.get("direction") or fire.get("side"),
        event=fire.get("event"),
        level=fire.get("entry") or fire.get("hunt", {}).get("level") if isinstance(fire.get("hunt"), dict) else fire.get("entry"),
        reasons=list(fire.get("why_state") or []),
        story_tf="15m",
        valid=True,
    )


def position_from_fire(
    fire: Dict[str, Any],
    *,
    trade_id: str,
    equity: float,
    risk_pct: float,
    opened_ts: Optional[int] = None,
) -> Position:
    entry = float(fire["entry"])
    sl = float(fire["stop"])
    tp = fire.get("target")
    sized = size_from_risk(equity, risk_pct, entry, sl)
    return Position(
        trade_id=trade_id,
        side=fire.get("direction") or fire.get("side"),
        entry=entry,
        sl=sl,
        tp=float(tp) if tp is not None else None,
        qty=sized["qty"],
        risk_usd=sized["risk_usd"],
        equity_at_entry=float(equity),
        atr=float(fire.get("atr_15m") or abs(entry - sl)),
        thesis=thesis_from_fire(fire),
        opened_ts=opened_ts,
        best=entry,
    )


def _r_multiple(pos: Position, price: float) -> float:
    risk = abs(pos.entry - pos.sl)
    if risk <= 0:
        return 0.0
    if pos.side == LONG:
        return (price - pos.entry) / risk
    return (pos.entry - price) / risk


def _trend_side(state: Optional[str]):
    s = (state or "").upper()
    if "BULL" in s:
        return LONG
    if "BEAR" in s:
        return SHORT
    return None


def reevaluate(
    pos: Position,
    *,
    price: float,
    high: Optional[float] = None,
    low: Optional[float] = None,
    structure_event: Optional[str] = None,
    structure_dir: Optional[str] = None,
    trend_1h_state: Optional[str] = None,
    level_lost: bool = False,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """One tick of after-FIRE Brain. Facts in, HOLD / TRAIL / EXIT out."""
    hi = high if high is not None else price
    lo = low if low is not None else price
    if pos.side == LONG:
        pos.best = max(pos.best or pos.entry, hi)
    else:
        pos.best = min(pos.best or pos.entry, lo)
    pos.r_now = _r_multiple(pos, price)

    log = {
        "lifecycle_version": LIFECYCLE_VERSION,
        "trade_id": pos.trade_id,
        "price": price,
        "r_now": pos.r_now,
        "thesis": pos.thesis.to_dict(),
        "now_ts": now_ts,
    }

    ev = (structure_event or "").upper()
    sd = (structure_dir or "").upper()
    against = (pos.side == LONG and sd in ("BEARISH", "SHORT", "DOWN")) or (
        pos.side == SHORT and sd in ("BULLISH", "LONG", "UP")
    )
    if ev in ("CHOCH", "CHoCH") and against:
        pos.thesis.valid = False
        pos.thesis.invalid_reason = "15m CHoCH against thesis"
        return {**log, "action": EXIT, "reason": pos.thesis.invalid_reason, "exit_kind": "THESIS_FAILURE"}

    h1 = _trend_side(trend_1h_state)
    if h1 and h1 != pos.side:
        pos.thesis.valid = False
        pos.thesis.invalid_reason = "1h trend flipped against thesis"
        return {**log, "action": EXIT, "reason": pos.thesis.invalid_reason, "exit_kind": "MARKET_REVERSAL"}

    if level_lost:
        pos.thesis.valid = False
        pos.thesis.invalid_reason = "entry level lost"
        return {**log, "action": EXIT, "reason": pos.thesis.invalid_reason, "exit_kind": "STRUCTURAL_INVALIDATION"}

    sl = pos.hard_sl()
    if pos.side == LONG and lo <= sl:
        return {**log, "action": EXIT, "reason": "hard SL", "exit_kind": "STRUCTURAL_INVALIDATION", "exit_px": sl}
    if pos.side == SHORT and hi >= sl:
        return {**log, "action": EXIT, "reason": "hard SL", "exit_kind": "STRUCTURAL_INVALIDATION", "exit_px": sl}

    if pos.tp is not None:
        if pos.side == LONG and hi >= pos.tp:
            return {**log, "action": EXIT, "reason": "target reached", "exit_kind": "TARGET_REACHED", "exit_px": pos.tp}
        if pos.side == SHORT and lo <= pos.tp:
            return {**log, "action": EXIT, "reason": "target reached", "exit_kind": "TARGET_REACHED", "exit_px": pos.tp}

    moved = False
    if pos.r_now >= 1.0 and pos.atr > 0:
        if pos.side == LONG:
            be = pos.entry
            trail = pos.best - pos.atr
            new_sl = max(pos.sl, be, trail)
            if pos.trail_sl is None or new_sl > pos.trail_sl:
                pos.trail_sl = new_sl
                pos.protected = True
                moved = True
        else:
            be = pos.entry
            trail = pos.best + pos.atr
            new_sl = min(pos.sl, be, trail)
            if pos.trail_sl is None or new_sl < pos.trail_sl:
                pos.trail_sl = new_sl
                pos.protected = True
                moved = True

    if moved:
        return {
            **log,
            "action": TRAIL,
            "reason": "protect profit / structure trail",
            "sl": pos.hard_sl(),
            "protected": True,
        }

    return {**log, "action": HOLD, "reason": "thesis still valid", "sl": pos.hard_sl()}
