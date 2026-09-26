"""Auto-trade: S1/S2 entry on each closed 5m + V1b lifecycle.

Paper always works. LIVE mode places real MEXC Isolated orders ONLY if both:
  1) CONFIG["mode"] == "LIVE" (the UI toggle), AND
  2) env var MEXC_LIVE_TRADING_ENABLED=true is set.
"""
from .autotrader_state import CONFIG, STATE, _live_armed, _open_auto  # noqa: F401
from .autotrader_sizing import (  # noqa: F401
    status, update, compute_sizing, _fetch_available_balance,
)
try:
    from .autotrader_sizing import _s1_levels  # noqa: F401
except ImportError:
    from .autotrader_sizing import _hunt_levels as _s1_levels  # noqa: F401
from .autotrader_loop import evaluate  # noqa: F401
from .autotrader_exec import _open_live_from_s1, _open_from_s1  # noqa: F401
from .autotrader_live_sync import _install_price_guard

_install_price_guard()
