"""Auto-trade: Hunt C-FI entry on each closed 5m + V1b lifecycle.

Paper always works. LIVE mode places real MEXC Isolated orders ONLY if both:
  1) CONFIG["mode"] == "LIVE" (the UI toggle), AND
  2) env var MEXC_LIVE_TRADING_ENABLED=true is set.
"""
from .autotrader_state import CONFIG, STATE, _live_armed, _open_auto  # noqa: F401
from .autotrader_sizing import (  # noqa: F401
    status, update, compute_sizing, _hunt_levels, _fetch_available_balance,
)
from .autotrader_loop import evaluate  # noqa: F401
from .autotrader_live_sync import _install_price_guard

_install_price_guard()
