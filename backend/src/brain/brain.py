"""PHASE G — BRAIN V2 (decision interpretation layer)."""
from typing import List, Dict, Optional
from ..config import CONFIG_VERSION, ASSUMPTIONS
from .. import settings
from ..contract import (LONG, SHORT, NEUTRAL, STATE_LONG, STATE_SHORT,
                        STATE_WAIT, STATE_AVOID, clamp)
from .confluence import find_confluence_zones
from .conflict import detect_conflict
from .mtf import apply_gate
from . import evidence as ev
from . import scoring

EXCLUDE_FROM_EXECUTION = scoring.EXCLUDE_FROM_EXECUTION

# The remainder of this file is loaded from main plus one trigger_score call change.
# If you are reading a stub, a follow-up commit restores the body.
