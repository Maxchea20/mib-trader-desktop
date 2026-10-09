"""Standalone New York opening-range breakout engine.

Does not import or modify Hunt, S1, S2, or the other analysis agents.
Live order submission is disabled unless a caller explicitly opts in.
"""

from .engine import OrbConfig, comparison_grid, primary_config, run_backtest
from .session import ORB_MINUTES

__all__ = [
    "ORB_MINUTES",
    "OrbConfig",
    "comparison_grid",
    "primary_config",
    "run_backtest",
]
