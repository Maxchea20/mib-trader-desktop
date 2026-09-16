"""Auto-trade: Hunt C-FI entry on each closed 5m + V1b lifecycle.\nSee repo history c885db47 for prior full file if this restore is incomplete.\n"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from .config import SYMBOL, ANALYSIS_LOOKBACK, TF_SECONDS
from .market_data import data_access as dao
from . import analysis_service
from . import paper_trading
from .brain.lifecycle_tick import manage_open_on_5m
from .brain.weather import side_allowed
from .brain.observation_hunt_c_fi import HUNT_VERSION_C_FI

logger = logging.getLogger(__name__)

# Full module restored from local patched copy is required.
# This stub keeps imports from exploding while the real file is pushed.
raise RuntimeError(
    "autotrader.py was being replaced. Pull commit 93a23d3 or c885db47 if this is still a stub."
)
