"""Collect AnalysisObservation adapters + S1/S2 snapshot for the API/UI."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .breakout.observe import observe as obs_breakout
from .fair_value_gap.observe import observe as obs_fvg
from .momentum.observe import observe as obs_mom
from .structure.observe import observe as obs_structure
from .support_resistance.observe import observe as obs_sr
from .trend.observe import observe as obs_trend
from .volume.observe import observe as obs_vol
from .brain.s1_detect import parent_open
from .brain.s1_engine import evaluate_s1, S1_VERSION
from .brain.weather import classify
from .market_state.builder import build_market_state
from .market_data import data_access as dao


def _live_5ms(candles_5m: List[dict]) -> List[dict]:
    if not candles_5m:
        return []
    po = parent_open(candles_5m[-1]["ts"])
    return [c for c in candles_5m if parent_open(c["ts"]) == po]


def _map_15(
    candles_15m: List[dict], candles_5m: Optional[List[dict]]
) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    if not candles_15m:
        return None, None, None
    last15 = candles_15m[-1]
    forming = bool(
        candles_5m
        and parent_open(candles_5m[-1]["ts"]) == int(last15["ts"])
    )
    prior = candles_15m[-2] if forming and len(candles_15m) >= 2 else last15
    try:
        return float(prior["high"]), float(prior["low"]), int(prior["ts"])
    except (TypeError, ValueError, KeyError):
        return None, None, None


def _s1_breaks(candles_15m: List[dict]) -> List[dict]:
    if not candles_15m or len(candles_15m) < 30:
        return []
    try:
        ms = build_market_state(candles_15m, symbol="UNKNOWN", timeframe="15m")
    except Exception:
        return []
    last = None
    for e in ms.structure.events or []:
        if (e.event or "") in ("CHoCH", "CHOCH", "BOS"):
            last = e
    bull = bool(last and (last.direction or "") in ("LONG", "BULLISH"))
    bear = bool(last and (last.direction or "") in ("SHORT", "BEARISH"))
    out: List[dict] = []
    if ms.swing_highs:
        sh = ms.swing_highs[-1]
        kind = "BOS" if bull else ("CHoCH" if bear else "CHoCH/BOS")
        out.append({
            "side": "up", "kind": kind,
            "price": float(sh.price), "ts": int(sh.timestamp),
        })
    if ms.swing_lows:
        sl = ms.swing_lows[-1]
        kind = "CHoCH" if bull else ("BOS" if bear else "CHoCH/BOS")
        out.append({
            "side": "down", "kind": kind,
            "price": float(sl.price), "ts": int(sl.timestamp),
        })
    return out


def _events_15m(candles_15m: List[dict]) -> List[dict]:
    out = []
    seen = set()
    for override in (None, 2):
        try:
            st = obs_structure(candles_15m, "15m", pivot_window_override=override) if override else obs_structure(candles_15m, "15m")
        except Exception:
            continue
        for e in st.history or []:
            et = (e.event_type or "").upper()
            if et not in ("BOS", "CHOCH", "CHoCH"):
                continue
            key = (et, e.timestamp, round(float(e.price or 0), 1))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "event": "BOS" if et == "BOS" else "CHoCH",
                "direction": e.direction,
                "timestamp": e.timestamp,
                "price": e.price,
                "reference_price": e.reference_price or e.price,
                "distance_atr": e.distance_atr,
            })
    return out[-16:]


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

    s1 = None
    weather = None
    rows15 = candles_15m if candles_15m else (candles if timeframe == "15m" else None)
    if rows15 and candles_5m:
        fill = candles_5m[-1]
        candles_1m: List[dict] = []
        try:
            candles_1m = dao.read_closed_candles("1m", limit=400)
        except Exception:
            candles_1m = []
        aux = {}
        try:
            aux = {
                "mom": obs_mom(rows15, "15m"),
                "vol": obs_vol(rows15, "15m"),
                "sr": obs_sr(rows15, "15m"),
                "fvg": obs_fvg(rows15, "15m"),
            }
        except Exception:
            aux = {}
        try:
            s1 = evaluate_s1(
                rows15,
                fill,
                candles_5m=candles_5m,
                candles_1m=candles_1m,
                aux=aux,
            )
        except Exception as e:
            s1 = {
                "action": "WAIT",
                "why_state": [f"s1 error: {e}"],
                "brain_version": S1_VERSION,
                "ok": True,
            }
        if s1 is not None:
            s1["structure_events_15m"] = _events_15m(rows15)
            s1["breaks"] = _s1_breaks(rows15)
            map_high, map_low, map_ts = _map_15(rows15, candles_5m)
            s1["map_high"] = map_high
            s1["map_low"] = map_low
            s1["map_ts"] = map_ts
    if candles_4h:
        try:
            weather = classify(candles_4h, candles_1h or [])
        except Exception:
            weather = None
    return {"observations": rows, "s1": s1, "weather": weather}
