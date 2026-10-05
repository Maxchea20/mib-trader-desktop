"""Swing AI configuration.  Every number is a plain default, not a tuned value."""
import os
from dataclasses import dataclass, field
from typing import Optional


def _first_model(value: str) -> str:
    """SWING_AI_MODEL takes ONE model name; if a list is written by mistake, the first entry is used."""
    return (value or "").split(",")[0].strip() or "gpt-5.4-mini"


@dataclass
class SwingConfig:
    # --- AI cadence ---
    heartbeat_seconds: int = 900          # normal full review
    management_seconds: int = 300         # open-position review
    min_seconds_between_ai_calls: int = 60  # debounce for event wakes (invalidation ignores it)
    event_cooldown_seconds: int = 900     # same wake kind is not re-fired within this (urgent wakes ignore it)
    price_move_trigger_pct: float = 0.6   # raw move over the window below wakes the AI (cost control, not an opinion)
    price_move_window_seconds: int = 1800
    stop_proximity_pct: float = 0.15      # open trade: price this close (% of price) to the stop wakes the AI
    model: str = field(default_factory=lambda: _first_model(os.environ.get("SWING_AI_MODEL")
                                                             or os.environ.get("AI_THESIS_MODEL", "gpt-5.4-mini")))
    llm_timeout_seconds: int = 180
    reasoning: Optional[str] = None       # None = model default; low | medium | high
    context: str = "FULL"                 # FULL | COMPACT candle history sent to the AI

    # --- deterministic risk layer (paper equity and limits) ---
    equity_usd: float = 10_000.0
    risk_pct: float = 1.0                 # of equity, per trade
    max_leverage: float = 5.0
    max_position_usd: float = 50_000.0
    min_rr: float = 1.5
    min_stop_pct: float = 0.3             # stop distance bounds as a percent of price (no volatility measure)
    max_stop_pct: float = 8.0
    max_entry_distance_pct: float = 3.0   # a limit entry no farther than this from live price
    market_tolerance_pct: float = 0.05    # entry this close to live price counts as a market order
    limit_expiry_minutes: int = 480
    max_spread_pct: float = 0.03
    min_confidence: float = 0.0           # recorded for calibration, not a gate: MIB does not veto the AI's own confidence
    max_trades_per_day: int = 3
    max_daily_loss_r: float = 3.0
    cooldown_after_loss_minutes: int = 60
    max_ticker_age_seconds: int = 30
    safety_mode: str = "STRICT"           # STRICT | RELAXED (paper only): RELAXED drops the discretionary limits below, never the structural checks
    live_risk_pct: float = 0.5            # LIVE only (panel setting): % of the real available balance risked per trade
    live_max_usd: float = 100.0           # LIVE only (panel setting): hard cap on position notional in USD
    live_allow_limit: bool = False        # LIVE only (panel setting): let resting LIMIT entries go to the exchange
    allow_reverse: bool = False           # AI may suggest a reversal; code never flips a position on its own

    # --- fees (MEXC BTCUSDT perpetual) ---
    taker_fee: float = 0.0002             # 0.02%: market and stop orders, and stop-loss exits
    maker_fee: float = 0.0                # 0%: resting limit orders and take-profit exits
    funding_rate_8h: float = 0.000049     # +0.0049% per 8h (MEXC BTCUSDT); longs pay shorts when positive.  Real settled rates are used when MEXC history is reachable


DEFAULT = SwingConfig()
