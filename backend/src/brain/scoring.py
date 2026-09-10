"""BRAIN V2 — Setup / Trigger / Location / Extension scoring."""
from typing import Dict, List
from . import evidence as ev
from ..contract import LONG, SHORT

EXCLUDE_FROM_EXECUTION = ("elliott_wave",)
_CLUSTER_ATR_MULT = 0.5
