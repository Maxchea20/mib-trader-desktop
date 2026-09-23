"""Post-FIRE Brain: one engine owns HOLD / EXIT.

No votes. No separate trade manager.
V1b: 1h alone does not exit. 15m CHoCH against does.
Trail is OFF — SL stays at the Hunt 1.5 ATR stop. Do not walk SL to BE
or best-ATR; that was cutting winners before TP.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ..contract import LONG, SHORT

LIFECYCLE_VERSION = "BRAIN_LIFECYCLE_V1B_NOTRAIL"
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
        # Trail disabled: always the original Hunt stop.
        return self.sl


def size_from_risk(equity: float, risk_pct: float, entry: float, sl: float) -> Dict[str, float]:
    """Capital -> risk -> SL distance -> qty. Never size first."""
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


def position_from_scenario_fire(
    scenario_result: Dict[str, Any],
    *,
    trade_id: str,
    equity: float,
    risk_pct: float,
    sl_atr_mult: float,
    tp_atr_mult: float,
    opened_ts: Optional[int] = None,
) -> Position:
    """Thin adapter: scenario_engine.py's FIRE output -> the existing,
    UNCHANGED Position/Thesis machinery, via the existing position_from_fire().

    scenario_engine.py deliberately has no SL/TP of its own (see its
    module docstring) -- entry timing and structural confluence are its
    only job. This function computes SL/TP from the caller-supplied ATR
    multiples (pass CONFIG["sl_atr_mult"]/["tp_atr_mult"] -- the same
    values already used by the legacy engine, currently 1.5/2.5, which
    also happen to match the convention this session's research used to
    validate C) and the fire's own atr15, then builds a plain dict in
    exactly the shape position_from_fire() already expects and delegates
    to it entirely. No new sizing/risk logic is introduced here.
    """
    entry = float(scenario_result["entry"])
    atr15 = scenario_result.get("atr15")
    if not atr15:
        raise ValueError("scenario fire missing a usable atr15 -- cannot size SL/TP; refusing to open blind")
    atr15 = float(atr15)
    side = scenario_result.get("direction")
    if side == LONG:
        sl = entry - sl_atr_mult * atr15
        tp = entry + tp_atr_mult * atr15
    elif side == SHORT:
        sl = entry + sl_atr_mult * atr15
        tp = entry - tp_atr_mult * atr15
    else:
        raise ValueError(f"scenario fire has unrecognized direction: {side!r}")

    fire = {
        "direction": side,
        "entry": entry,
        "stop": sl,
        "target": tp,
        "atr_15m": atr15,
        "event": scenario_result.get("origin_event") or scenario_result.get("scenario"),
        "why_state": [scenario_result.get("reason")] if scenario_result.get("reason") else [],
    }
    return position_from_fire(fire, trade_id=trade_id, equity=equity, risk_pct=risk_pct, opened_ts=opened_ts)


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
    """One tick of after-FIRE Brain. Facts in, HOLD / EXIT out. No trail."""
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
    log["h1_warning"] = bool(h1 and h1 != pos.side)

    if level_lost:
        pos.thesis.valid = False
        pos.thesis.invalid_reason = "originating structure lost"
        return {**log, "action": EXIT, "reason": pos.thesis.invalid_reason, "exit_kind": "STRUCTURAL_INVALIDATION"}

    sl = pos.hard_sl()
    if pos.side == LONG and lo <= sl:
        return {**log, "action": EXIT, "reason": "hard SL", "exit_kind": "HARD_SL", "exit_px": sl}
    if pos.side == SHORT and hi >= sl:
        return {**log, "action": EXIT, "reason": "hard SL", "exit_kind": "HARD_SL", "exit_px": sl}

    if pos.tp is not None:
        if pos.side == LONG and hi >= pos.tp:
            return {**log, "action": EXIT, "reason": "target reached", "exit_kind": "TARGET_REACHED", "exit_px": pos.tp}
        if pos.side == SHORT and lo <= pos.tp:
            return {**log, "action": EXIT, "reason": "target reached", "exit_kind": "TARGET_REACHED", "exit_px": pos.tp}

    return {**log, "action": HOLD, "reason": "thesis still valid", "sl": sl}