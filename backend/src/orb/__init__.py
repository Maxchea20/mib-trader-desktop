"""Standalone New York opening-range breakout engine.

Does not import or modify Hunt, S1, S2, or the other analysis agents.
Live order submission is disabled unless a caller explicitly opts in.
ORB V2 and ORB V3 are separate strategies in this package. Neither submits orders.
V3 does not change the V2 range or the original engine.
"""

from .engine import OrbConfig, comparison_grid, primary_config, run_backtest
from .session import ORB_MINUTES
from .v2 import OrbV2Config, run_v2_backtest, v2_primary_config
from .v3 import OrbV3Config, run_v3_backtest, v3_primary_config, v3b_primary_config

__all__ = [
    "ORB_MINUTES",
    "OrbConfig",
    "OrbV2Config",
    "OrbV3Config",
    "comparison_grid",
    "primary_config",
    "run_backtest",
    "run_v2_backtest",
    "run_v3_backtest",
    "v2_primary_config",
    "v3_primary_config",
    "v3b_primary_config",
]
