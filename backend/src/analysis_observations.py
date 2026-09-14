"""Collect AnalysisObservation adapters + Hunt C snapshot for the API/UI."""
from __future__ import annotations

from typing import Dict, List, Optional

from .breakout.observe import observe as obs_breakout
from .fair_value_gap.observe import observe as obs_fvg
from .momentum.observe import observe as obs_mom
from .structure.observe import observe as obs_structure
from .support_resistance.observe import observe as obs_sr
from .trend.observe import observe as obs_trend
from .volume.observe import observe as obs_vol
from .brain.observation_hunt_c import evaluate_hunt_c, parent_open, HUNT_VERSION_C
from .brain.weather import classify


def _live_5ms(candles_5m: List[dict]) -> List[dict]:
    if not candles_5m:
        return []
    po = parent_open(candles_5m[-1]["ts"])
    return [c for c in candles_5m if parent_open(c["ts"]) == po]


def collect_observations(
    candles: List[dict],
    timeframe: str,
    candles_5m: Optional[List[dict]] = None,
    candles_4h: Optional[List[dict]] = None,
    candles_1h: Optional[List[dict]] = None,
    candles_15m: Optional[List[dict]] = None,
) -> Dict:
    rows = []
    watchers = [
        ("market_structure", obs_structure),
        ("breakout", obs_breakout),
        ("support_resistance", obs_sr),
        ("fair_value_gap", obs_fvg),
        ("volume", obs_vol),
        ("trend", obs_trend),
        ("momentum", obs_mom),
    ]
    for name, fn in watchers:
        try:
            obs = fn(candles, timeframe)
            rows.append(obs.to_dict())
        except Exception as e:
            rows.append({"source": name, "state": "ERROR", "notes": [str(e)], "valid": False})

    hunt = None
    weather = None
    hunt_15 = candles_15m if candles_15m else (candles if timeframe == "15m" else None)
    if hunt_15 and candles_5m:
        fill = candles_5m[-1]
        live = _live_5ms(candles_5m)
        try:
            hunt = evaluate_hunt_c(
                hunt_15,
                fill,
                live_5ms=live,
                candles_4h=candles_4h,
                candles_1h=candles_1h,
                candles_5m=candles_5m,
            )
        except Exception as e:
            hunt = {
                "action": "WAIT",
                "why_state": [f"hunt C error: {e}"],
                "brain_version": HUNT_VERSION_C,
            }
    if candles_4h:
        try:
            weather = classify(candles_4h, candles_1h or [])
        except Exception:
            weather = None
    return {"observations": rows, "hunt": hunt, "weather": weather}
