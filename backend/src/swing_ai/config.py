"""Swing AI configuration.  Every number is a plain default, not a tuned value."""
import os
from dataclasses import dataclass, field


@dataclass
class SwingConfig:
    # --- AI cadence ---
    heartbeat_seconds: int = 900          # normal full review
    management_seconds: int = 300         # open-position review
    min_seconds_between_ai_calls: int = 60  # debounce for event wakes (invalidation ignores it)
    event_cooldown_seconds: int = 900     # same event kind on the same timeframe is not re-fired within this
    model: str = field(default_factory=lambda: os.environ.get("SWING_AI_MODEL")
                       or os.environ.get("AI_THESIS_MODEL", "gpt-5.4-mini"))
    llm_timeout_seconds: int = 90

    # --- deterministic risk layer (paper equity and limits) ---
    equity_usd: float = 10_000.0
    risk_pct: float = 1.0                 # of equity, per trade
    max_leverage: float = 5.0
    max_position_usd: float = 50_000.0
    min_rr: float = 1.5
    min_stop_atr1h: float = 0.5           # stop distance bounds, in 1h ATR
    max_stop_atr1h: float = 5.0
    max_entry_distance_atr1h: float = 2.0  # limit entry no farther than this from live price
    market_tolerance_atr15: float = 0.15  # entry this close to live price = market order
    limit_expiry_minutes: int = 480
    max_spread_pct: float = 0.03
    min_confidence: float = 0.5
    max_trades_per_day: int = 3
    max_daily_loss_r: float = 3.0
    cooldown_after_loss_minutes: int = 60
    max_ticker_age_seconds: int = 30
    allow_reverse: bool = False           # AI may suggest a reversal; code never flips a position on its own

    # --- fees (MEXC BTCUSDT perpetual) ---
    taker_fee: float = 0.0002
    maker_fee: float = 0.0


DEFAULT = SwingConfig()
