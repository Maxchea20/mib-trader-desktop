"""Brain — final decision engine package (NOT an 11th analysis agent)."""
from .hunt_brain import evaluate_hunt, reset_hunt_state, VERSION as HUNT_VERSION, SL_ATR, TP_ATR
# Compatibility exports for older callers; live authority is Hunt.
evaluate_s1 = evaluate_hunt
S1_VERSION = HUNT_VERSION
from .weather import classify as classify_weather, side_allowed, WEATHER_VERSION
from .lifecycle import (
    LIFECYCLE_VERSION,
    HOLD,
    TRAIL,
    EXIT,
    reevaluate,
    position_from_fire,
    thesis_from_fire,
    size_from_risk,
)
