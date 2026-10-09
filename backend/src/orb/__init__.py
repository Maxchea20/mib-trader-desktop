"""Standalone New York opening-range breakout engine.

Does not import or modify Hunt, S1, S2, or the other analysis agents.
Live order submission is disabled unless a caller explicitly opts in.
ORB V2 is a separate strategy in this package. It does not submit orders.
"""

from .engine import OrbConfig, comparison_grid, primary_config, run_backtest
from .session import ORB_MINUTES
from .v2 import OrbV2Config, run_v2_backtest, v2_primary_config

__all__ = [
    "ORB_MINUTES",
    "OrbConfig",
    "OrbV2Config",
    "comparison_grid",
    "primary_config",
    "run_backtest",
    "run_v2_backtest",
    "v2_primary_config",
]
