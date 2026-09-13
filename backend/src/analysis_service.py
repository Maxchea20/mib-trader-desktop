"""Analysis orchestration: run the 10 agents + HTF regime + Brain + observations."""
from typing import List, Dict
import numpy as np

from .config import SYMBOL, HTF_TIMEFRAMES, ANALYSIS_LOOKBACK
from .market_data.closed_candles import filter_closed
from .market_state.actionable import apply_actionable_structure
from . import settings
from .contract import AgentResult
from .market_data import data_access as dao
from .brain import brain as brain_engine
from .brain.mtf import regime_from_agents
from .market_state.builder import build_market_state
from .analysis_observations import collect_observations

from . import structure, breakout, fibonacci, elliott_wave, volume
from . import momentum, support_resistance, trend, pattern, fair_value_gap

AGENT_REGISTRY = {
    "market_structure": structure.analyze,
    "breakout": breakout.analyze,
    "fibonacci": fibonacci.analyze,
    "elliott_wave": elliott_wave.analyze,
    "volume": volume.analyze,
    "momentum": momentum.analyze,
    "support_resistance": support_resistance.analyze,
    "trend": trend.analyze,
    "pattern": pattern.analyze,
    "fair_value_gap": fair_value_gap.analyze,
}
AGENT_ORDER = list(AGENT_REGISTRY.keys())


def _native(obj):
    if isinstance(obj, dict):
        return {k: _native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_native(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return _native(obj.tolist())
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return _native(asdict(obj))
    return obj


def _market_state_to_dict(state) -> Dict:
    return {
        "symbol": state.symbol,
        "timeframe": state.timeframe,
        "timestamp": state.timestamp,
        "price": state.price,
        "structure": {
            "direction": state.structure.direction,
            "regime": state.structure.regime,
            "structure_sequence": state.structure.structure_sequence,
            "hh": state.structure.hh, "hl": state.structure.hl,
            "lh": state.structure.lh, "ll": state.structure.ll,
            "last_high": state.structure.last_high,
            "previous_high": state.structure.previous_high,
            "last_low": state.structure.last_low,
            "previous_low": state.structure.previous_low,
            "actionable_state": getattr(state.structure, "actionable_state", "NONE"),
            "actionable_level": getattr(state.structure, "actionable_level", None),
            "m5_confirm": getattr(state.structure, "m5_confirm", "NONE"),
            "event": {
                "event": state.structure.event.event,
                "direction": state.structure.event.direction,
                "price": state.structure.event.price,
                "timestamp": state.structure.event.timestamp,
            },
            "events": [
                {"event": e.event, "direction": e.direction, "price": e.price, "timestamp": e.timestamp}
                for e in state.structure.events
            ],
        },
        "location": {
            "support": state.location.support,
            "resistance": state.location.resistance,
            "near_support": state.location.near_support,
            "near_resistance": state.location.near_resistance,
        },
        "volatility": {
            "atr": state.volatility.atr,
            "atr_pct": state.volatility.atr_pct,
        },
        "market_phase": state.market_phase,
    }


def run_agents(candles, timeframe, market_state=None, pivot_window_override=None):
    results = []
    for aid in AGENT_ORDER:
        try:
            analyzer = AGENT_REGISTRY[aid]
            if aid == "market_structure":
                res = analyzer(candles, timeframe, market_state=market_state,
                               pivot_window_override=pivot_window_override)
            else:
                res = analyzer(candles, timeframe)
        except Exception as e:
            from .contract import neutral
            res = neutral(aid, timeframe, f"error: {e}", valid=False)
        results.append(res)
    return results


def _htf_regime() -> Dict:
    agents_by_tf = {}
    for tf in HTF_TIMEFRAMES:
        candles = dao.read_closed_candles(tf, limit=ANALYSIS_LOOKBACK)
        if len(candles) < 60:
            continue
        agents_by_tf[tf] = run_agents(candles, tf)
    if not agents_by_tf:
        return {"regime": "NEUTRAL", "regime_score": 0.0, "per_timeframe": {}}
    return regime_from_agents(agents_by_tf)


def full_analysis(timeframe: str) -> Dict:
    candles = dao.read_closed_candles(timeframe, limit=ANALYSIS_LOOKBACK)
    if len(candles) < 30:
        return {"error": "insufficient_data", "timeframe": timeframe, "candles": len(candles)}
    price = float(candles[-1]["close"])
    m5_closed = None
    c4 = None
    c1h = None
    if timeframe == "15m":
        m5_closed = filter_closed(dao.read_candles("5m", limit=ANALYSIS_LOOKBACK * 3), "5m")
        c4 = dao.read_closed_candles("4h", limit=300)
        c1h = dao.read_closed_candles("1h", limit=400)
    market_state = build_market_state(candles, symbol=SYMBOL, timeframe=timeframe)
    apply_actionable_structure(
        market_state.structure, candles, market_state.volatility.atr,
        m5_candles=m5_closed, timeframe=timeframe,
    )
    agents = run_agents(candles, timeframe, market_state=market_state)
    htf = _htf_regime()
    brain = brain_engine.decide(agents, price, timeframe, htf, atr_value=market_state.volatility.atr)
    pack = collect_observations(candles, timeframe, candles_5m=m5_closed, candles_4h=c4, candles_1h=c1h)
    return _native({
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "price": price,
        "candle_count": len(candles),
        "market_state": _market_state_to_dict(market_state),
        "agents": [a.to_dict() for a in agents],
        "observations": pack.get("observations") or [],
        "hunt": pack.get("hunt"),
        "weather": pack.get("weather"),
        "agent_weights": settings.weights(),
        "htf_regime": htf,
        "brain": brain,
    })


def single_agent(agent_id: str, timeframe: str) -> Dict:
    if agent_id not in AGENT_REGISTRY:
        return {"error": "unknown_agent"}
    candles = dao.read_closed_candles(timeframe, limit=ANALYSIS_LOOKBACK)
    if len(candles) < 30:
        return {"error": "insufficient_data"}
    if agent_id == "market_structure":
        m5_closed = None
        if timeframe == "15m":
            m5_closed = filter_closed(dao.read_candles("5m", limit=ANALYSIS_LOOKBACK * 3), "5m")
        market_state = build_market_state(candles, symbol=SYMBOL, timeframe=timeframe)
        apply_actionable_structure(
            market_state.structure, candles, market_state.volatility.atr,
            m5_candles=m5_closed, timeframe=timeframe,
        )
        result = AGENT_REGISTRY[agent_id](candles, timeframe, market_state=market_state)
    else:
        result = AGENT_REGISTRY[agent_id](candles, timeframe)
    return _native(result.to_dict())
