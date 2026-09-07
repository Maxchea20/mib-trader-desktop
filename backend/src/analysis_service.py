"""Analysis orchestration: run the 10 agents + HTF regime + Brain."""
from typing import List, Dict
import numpy as np

from .config import SYMBOL, HTF_TIMEFRAMES, ANALYSIS_LOOKBACK
from . import settings
from .contract import AgentResult
from .market_data import data_access as dao
from .brain import brain as brain_engine
from .brain.mtf import regime_from_agents
from .market_state.builder import build_market_state

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
    """Recursively convert numpy types to native Python for JSON serialization."""
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

    # Support dataclasses such as MarketState / SwingPoint if they
    # are ever passed directly into the serializer.
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return _native(asdict(obj))

    return obj


def _market_state_to_dict(state) -> Dict:
    """Expose the shared MarketState in a frontend/API-safe structure."""
    return {
        "symbol": state.symbol,
        "timeframe": state.timeframe,
        "timestamp": state.timestamp,
        "price": state.price,

        "structure": {
            "direction": state.structure.direction,
            "regime": state.structure.regime,
            "structure_sequence": state.structure.structure_sequence,

            "hh": state.structure.hh,
            "hl": state.structure.hl,
            "lh": state.structure.lh,
            "ll": state.structure.ll,

            "last_high": state.structure.last_high,
            "previous_high": state.structure.previous_high,
            "last_low": state.structure.last_low,
            "previous_low": state.structure.previous_low,

            "swing_high_strength": state.structure.swing_high_strength,
            "swing_low_strength": state.structure.swing_low_strength,

            "break_distance_atr": state.structure.break_distance_atr,

            "event": {
    "event": state.structure.event.event,
    "direction": state.structure.event.direction,
    "price": state.structure.event.price,
    "timestamp": state.structure.event.timestamp,
    "reference_price": state.structure.event.reference_price,
    "swing_index": state.structure.event.swing_index,
    "distance_atr": state.structure.event.distance_atr,
},

"events": [
    {
        "event": e.event,
        "direction": e.direction,
        "price": e.price,
        "timestamp": e.timestamp,
        "reference_price": e.reference_price,
        "swing_index": e.swing_index,
        "distance_atr": e.distance_atr,
    }
    for e in state.structure.events
],
        },

        "swings": {
            "highs": [
                {
                    "index": swing.index,
                    "timestamp": swing.timestamp,
                    "price": swing.price,
                    "kind": swing.kind,
                    "strength": swing.strength,
                }
                for swing in state.swing_highs
            ],
            "lows": [
                {
                    "index": swing.index,
                    "timestamp": swing.timestamp,
                    "price": swing.price,
                    "kind": swing.kind,
                    "strength": swing.strength,
                }
                for swing in state.swing_lows
            ],
        },

        "location": {
            "support": state.location.support,
            "resistance": state.location.resistance,
            "distance_to_support_atr": state.location.distance_to_support_atr,
            "distance_to_resistance_atr": state.location.distance_to_resistance_atr,
            "near_support": state.location.near_support,
            "near_resistance": state.location.near_resistance,
            "liquidity_high": state.location.liquidity_high,
            "liquidity_low": state.location.liquidity_low,
        },

        "volatility": {
            "atr": state.volatility.atr,
            "atr_pct": state.volatility.atr_pct,
            "range_high": state.volatility.range_high,
            "range_low": state.volatility.range_low,
            "range_position_pct": state.volatility.range_position_pct,
            "compression_pct": state.volatility.compression_pct,
        },

        "support": list(state.support),
        "resistance": list(state.resistance),
        "market_phase": state.market_phase,
        "metadata": dict(state.metadata),
    }


def run_agents(
    candles: List[Dict],
    timeframe: str,
    market_state=None,
) -> List[AgentResult]:
    results = []

    for aid in AGENT_ORDER:
        try:
            analyzer = AGENT_REGISTRY[aid]

            # Market Structure already accepts the shared MarketState.
            # Keep the compatibility fallback for the other agents until
            # they are migrated to the shared-state architecture.
            if aid == "market_structure":
                res = analyzer(
                    candles,
                    timeframe,
                    market_state=market_state,
                )
            else:
                res = analyzer(candles, timeframe)

        except Exception as e:
            from .contract import neutral
            res = neutral(
                aid,
                timeframe,
                f"error: {e}",
                valid=False,
            )

        results.append(res)

    return results


def _htf_regime() -> Dict:
    agents_by_tf = {}

    for tf in HTF_TIMEFRAMES:
        candles = dao.read_candles(
            tf,
            limit=ANALYSIS_LOOKBACK,
        )

        if len(candles) < 60:
            continue

        agents_by_tf[tf] = run_agents(candles, tf)

    if not agents_by_tf:
        return {
            "regime": "NEUTRAL",
            "regime_score": 0.0,
            "per_timeframe": {},
        }

    return regime_from_agents(agents_by_tf)


def full_analysis(timeframe: str) -> Dict:
    candles = dao.read_candles(
        timeframe,
        limit=ANALYSIS_LOOKBACK,
    )

    if len(candles) < 30:
        return {
            "error": "insufficient_data",
            "timeframe": timeframe,
            "candles": len(candles),
        }

    price = float(candles[-1]["close"])

    # Build ONE shared MarketState for the current timeframe.
    # This becomes the common source of truth for structure/location/
    # volatility information.
    market_state = build_market_state(
        candles,
        symbol=SYMBOL,
        timeframe=timeframe,
    )

    agents = run_agents(
        candles,
        timeframe,
        market_state=market_state,
    )

    htf = _htf_regime()

    brain = brain_engine.decide(
        agents,
        price,
        timeframe,
        htf,
    )

    return _native({
        "symbol": SYMBOL,
        "timeframe": timeframe,
        "price": price,
        "candle_count": len(candles),

        # Shared MarketState exposed to the frontend.
        "market_state": _market_state_to_dict(market_state),

        "agents": [
            a.to_dict()
            for a in agents
        ],

        "agent_weights": settings.weights(),

        "htf_regime": htf,

        "brain": brain,
    })


def single_agent(agent_id: str, timeframe: str) -> Dict:
    if agent_id not in AGENT_REGISTRY:
        return {"error": "unknown_agent"}

    candles = dao.read_candles(
        timeframe,
        limit=ANALYSIS_LOOKBACK,
    )

    if len(candles) < 30:
        return {"error": "insufficient_data"}

    # Give Market Structure the same shared-state source when it is
    # requested individually.
    if agent_id == "market_structure":
        market_state = build_market_state(
            candles,
            symbol=SYMBOL,
            timeframe=timeframe,
        )

        result = AGENT_REGISTRY[agent_id](
            candles,
            timeframe,
            market_state=market_state,
        )
    else:
        result = AGENT_REGISTRY[agent_id](
            candles,
            timeframe,
        )

    return _native(result.to_dict())