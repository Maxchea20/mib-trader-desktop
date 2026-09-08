from fastapi import FastAPI, APIRouter, Query, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
import os
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from src.config import (SYMBOL, TIMEFRAMES, AGENT_WEIGHTS, HTF_GATE, CONFLUENCE,
                        CONFLICT, ENTRY, CONFIG_VERSION, ASSUMPTIONS, HTF_TIMEFRAMES,
                        LTF_TIMEFRAMES)
from src.market_data import data_access as dao
from src.market_data import gap_sync, manager
from src import analysis_service
from src import settings as runtime_settings
from src import backtest as backtest_engine
from src import backtest_log
from src import backtest_walkforward
from src import walkforward_log
from src import paper_trading
from src import autotrader
from pydantic import BaseModel
from typing import Optional, Dict, List

app = FastAPI(title="MIB-Trader API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("mib-trader")


@api_router.get("/")
async def root():
    return {"app": "MIB-Trader", "version": CONFIG_VERSION, "symbol": SYMBOL,
            "timeframes": TIMEFRAMES}


@api_router.get("/config")
async def get_config():
    return {
        "config_version": CONFIG_VERSION,
        "symbol": SYMBOL,
        "timeframes": TIMEFRAMES,
        "htf_timeframes": HTF_TIMEFRAMES,
        "ltf_timeframes": LTF_TIMEFRAMES,
        "agent_weights": AGENT_WEIGHTS,
        "htf_gate": HTF_GATE,
        "confluence": CONFLUENCE,
        "conflict": CONFLICT,
        "entry": ENTRY,
        "assumptions": ASSUMPTIONS,
    }


# --- Market data (infrastructure) ---------------------------------------
@api_router.get("/market/ticker")
async def market_ticker():
    return manager.live_status()


@api_router.get("/market/candles")
async def market_candles(timeframe: str = Query("15m"), limit: int = Query(500)):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe", "timeframes": TIMEFRAMES}
    candles = dao.read_candles(timeframe, limit=limit)
    live = manager.live_status()
    return {"symbol": SYMBOL, "timeframe": timeframe, "count": len(candles),
            "candles": candles, "live": live}


@api_router.get("/market/sync-status")
async def market_sync_status():
    status = dao.read_sync_status()
    for tf, info in status["timeframes"].items():
        info["gaps"] = len(gap_sync.detect_gaps(SYMBOL, tf))
    status["live"] = manager.live_status()
    return status


@api_router.post("/market/sync")
async def market_sync(timeframe: str = Query(...)):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe"}
    return await gap_sync.sync_timeframe(SYMBOL, timeframe, limit=1000)


@api_router.get("/market/gaps")
async def market_gaps(timeframe: str = Query("15m")):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe"}
    return {"timeframe": timeframe, "gaps": gap_sync.detect_gaps(SYMBOL, timeframe)}


# --- Analysis (10 agents + Brain) ---------------------------------------
@api_router.get("/analysis")
async def analysis(timeframe: str = Query("15m")):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe", "timeframes": TIMEFRAMES}
    return analysis_service.full_analysis(timeframe)


@api_router.get("/agents/{agent_id}")
async def agent_detail(agent_id: str, timeframe: str = Query("15m")):
    return analysis_service.single_agent(agent_id, timeframe)


# --- Live config tuning --------------------------------------------------
class SettingsUpdate(BaseModel):
    agent_weights: Optional[Dict[str, float]] = None
    htf_gate: Optional[Dict[str, float]] = None
    confluence: Optional[Dict[str, float]] = None
    conflict: Optional[Dict[str, float]] = None
    entry: Optional[Dict[str, float]] = None


@api_router.get("/settings")
async def get_settings():
    return runtime_settings.snapshot()


@api_router.put("/settings")
async def put_settings(payload: SettingsUpdate):
    return runtime_settings.update(payload.model_dump(exclude_none=True))


@api_router.post("/settings/reset")
async def reset_settings():
    return runtime_settings.reset()


# --- Backtesting ---------------------------------------------------------
@api_router.get("/backtest")
async def backtest(timeframe: str = Query("15m"), lookback: int = Query(150),
                   forward: int = Query(8), step: int = Query(3)):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe", "timeframes": TIMEFRAMES}
    import asyncio
    result = await asyncio.to_thread(backtest_engine.run_backtest, timeframe,
                                     lookback, forward, step)
    if "summary" in result:
        # Auto-log every run (not just ones you explicitly save) so nothing
        # gets lost — you can always clear old ones later.
        try:
            backtest_log.record(timeframe, lookback, result["summary"])
        except Exception:
            pass  # logging a run should never break returning its result
    return result


@api_router.get("/backtest/log")
async def get_backtest_log(timeframe: Optional[str] = Query(None), limit: int = Query(100)):
    return {"runs": backtest_log.list_runs(timeframe, limit)}


@api_router.delete("/backtest/log/{run_id}")
async def delete_backtest_log_entry(run_id: str):
    ok = backtest_log.delete_run(run_id)
    return {"ok": ok}


@api_router.delete("/backtest/log")
async def clear_backtest_log(timeframe: Optional[str] = Query(None)):
    n = backtest_log.clear(timeframe)
    return {"ok": True, "deleted": n}


# --- Walk-forward backtest (real bar-by-bar SL/TP simulation) -----------
# Ported from mib-gold-MT5's backtest harness design: "same engines,
# consensus... as live" — this calls the exact same analysis_service /
# brain functions autotrader.py uses, rather than a separate simplified
# test-only implementation. Runs as a background job because a multi-day
# walk-forward run (re-running all 10 agents on every single bar) is too
# slow to hold an HTTP request open for.
class WalkForwardRunReq(BaseModel):
    timeframe: str = "15m"
    days: float = 7.0
    start_balance: float = 250.0
    capital_pct: float = 10.0
    leverage: float = 5.0
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    weights: Optional[Dict[str, float]] = None  # test a hypothetical weight
    # set for this run only — never touches your live settings. Omit to
    # use whatever your live weights currently are.
    htf_timeframes: Optional[List[str]] = None  # e.g. ["1h","4h"] or
    # ["30m","1h","4h","1d"] — independent of the live HTF_TIMEFRAMES
    # config. Omit to use the live default (4h + 1d).
    use_htf_gate: bool = True  # False = ignore the regime gate entirely
    # for this run (every bar treated as regime=NEUTRAL, i.e. no extra
    # score/confidence requirement either way).
    entry: Optional[Dict[str, float]] = None       # test hypothetical
    # Entry Thresholds (direction_min_score, entry_min_score,
    # entry_min_confidence, min_valid_agents, max_extension_pct) for this
    # run only. Omit to use live Settings values.
    conflict: Optional[Dict[str, float]] = None    # test hypothetical
    # Conflict/Contradiction settings for this run only. Omit for live.
    htf_gate_values: Optional[Dict[str, float]] = None  # test hypothetical
    # HTF Gate magnitudes (regime_min_score, counter_trend_penalty, etc.)
    # for this run only — separate from htf_timeframes above, which
    # controls WHICH timeframes feed the gate, not these numeric knobs.
    # Omit for live.
    pivot_window_override: Optional[int] = None  # None = use the new
    # dynamic per-timeframe swing-detection window (the live default,
    # anchored so 15m stays at window=5, unchanged). Pass an int (e.g. 5)
    # to force the OLD fixed window for an A/B comparison against the
    # new dynamic default — this is a structural-detection parameter,
    # not one of the four Settings-page panels, but gets the same
    # test-safely-never-touches-live treatment.


@api_router.post("/backtest/walkforward/run")
async def walkforward_run(req: WalkForwardRunReq):
    if req.timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe", "timeframes": TIMEFRAMES}
    if req.htf_timeframes:
        bad = [t for t in req.htf_timeframes if t not in TIMEFRAMES]
        if bad:
            return {"error": "invalid_htf_timeframes", "bad": bad, "timeframes": TIMEFRAMES}
    weights = req.weights
    if weights is not None:
        # Same bound as the live Settings panel enforces — a backtest
        # testing a hypothetical weight shouldn't be able to try something
        # (e.g. an accidental 50, or a negative value that would flip an
        # engine's vote) that live trading itself would never allow.
        weights = {
            k: max(runtime_settings.WEIGHT_MIN, min(runtime_settings.WEIGHT_MAX, float(v)))
            for k, v in weights.items()
        }
    # Entry/Conflict/HTF-gate overrides intentionally get NO bound
    # clamping here, unlike weights — their valid ranges vary wildly by
    # field (a percentage vs. a ratio vs. a raw score) and guessing wrong
    # bounds would be worse than no bounds. Merge onto live values instead
    # of requiring the full dict, so a partial override (e.g. just
    # entry_min_score) doesn't silently zero out every other key.
    def _merged_override(custom, live_getter):
        if not custom:
            return None
        base = dict(live_getter())
        base.update({k: float(v) for k, v in custom.items() if k in base})
        return base

    entry_override = _merged_override(req.entry, runtime_settings.entry)
    conflict_override = _merged_override(req.conflict, runtime_settings.conflict)
    htf_gate_override = _merged_override(req.htf_gate_values, runtime_settings.htf_gate)

    bt = backtest_walkforward.WalkForwardBacktest(
        timeframe=req.timeframe, days=req.days, start_balance=req.start_balance,
        capital_pct=req.capital_pct, leverage=req.leverage,
        sl_atr_mult=req.sl_atr_mult, tp_atr_mult=req.tp_atr_mult,
        weights_override=weights, htf_timeframes=req.htf_timeframes,
        use_htf_gate=req.use_htf_gate, entry_override=entry_override,
        conflict_override=conflict_override, htf_gate_override=htf_gate_override,
        pivot_window_override=req.pivot_window_override,
    )
    run_id = backtest_walkforward.runner.start(bt)
    return {"id": run_id, "status": "started"}


@api_router.get("/backtest/walkforward/status/{run_id}")
async def walkforward_status(run_id: str):
    bt = backtest_walkforward.runner.get(run_id)
    if not bt:
        return {"error": "run_not_found"}
    return {"id": bt.id, "status": bt.status, "progress": round(bt.progress, 3), "error": bt.error}


@api_router.get("/backtest/walkforward/result/{run_id}")
async def walkforward_result(run_id: str):
    bt = backtest_walkforward.runner.get(run_id)
    if not bt:
        return {"error": "run_not_found"}
    if bt.status not in ("done", "error", "stopped"):
        return {"error": "not_finished", "status": bt.status, "progress": round(bt.progress, 3)}
    if bt.error:
        return {"error": bt.error}
    # Log on first successful fetch only — repeated polling/re-fetching of
    # the same finished run should never create duplicate log rows.
    if not getattr(bt, "_logged", False):
        try:
            walkforward_log.record(bt.result)
        except Exception:
            pass
        bt._logged = True
    return bt.result


@api_router.get("/backtest/walkforward/log")
async def get_walkforward_log(limit: int = Query(100)):
    return {"runs": walkforward_log.list_runs(limit)}


@api_router.delete("/backtest/walkforward/log/{run_id}")
async def delete_walkforward_log_entry(run_id: str):
    return {"ok": walkforward_log.delete_run(run_id)}


@api_router.delete("/backtest/walkforward/log")
async def clear_walkforward_log():
    return {"ok": True, "deleted": walkforward_log.clear()}


@api_router.post("/backtest/walkforward/stop/{run_id}")
async def walkforward_stop(run_id: str):
    bt = backtest_walkforward.runner.get(run_id)
    if not bt:
        return {"error": "run_not_found"}
    bt.stop()
    return {"ok": True}


# --- Paper trading -------------------------------------------------------
class OpenTradeReq(BaseModel):
    side: str
    entry_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    notional_usd: float = 1000.0
    timeframe: str = ""
    brain_state: str = ""
    consensus: float = 0.0
    confidence: float = 0.0
    note: str = ""


class CloseTradeReq(BaseModel):
    exit_price: Optional[float] = None


@api_router.post("/paper/open")
async def paper_open(req: OpenTradeReq):
    live = manager.live_status()
    entry = req.entry_price if req.entry_price else live.get("last_price")
    if not entry:
        return {"error": "no_price_available"}
    return paper_trading.open_trade(
        symbol=SYMBOL, side=req.side, entry_price=entry, sl_price=req.sl_price,
        tp_price=req.tp_price, notional_usd=req.notional_usd, timeframe=req.timeframe,
        brain_state=req.brain_state, consensus=req.consensus, confidence=req.confidence,
        note=req.note,
    )


@api_router.post("/paper/close/{trade_id}")
async def paper_close(trade_id: str, req: CloseTradeReq):
    live = manager.live_status()
    exit_price = req.exit_price if req.exit_price else live.get("last_price")
    if not exit_price:
        return {"error": "no_price_available"}
    t = paper_trading.close_trade(trade_id, exit_price, "MANUAL")
    if not t:
        return {"error": "trade_not_found"}
    return t


@api_router.get("/paper/trades")
async def paper_trades(status: Optional[str] = Query(None)):
    live = manager.live_status()
    return {"trades": paper_trading.list_trades(status, live.get("last_price")),
            "live_price": live.get("last_price")}


@api_router.get("/paper/stats")
async def paper_stats():
    return paper_trading.stats()


@api_router.get("/paper/balance")
async def paper_balance():
    live = manager.live_status()
    return paper_trading.get_balance(live.get("last_price"))


class SetPaperBalanceReq(BaseModel):
    amount: float


@api_router.post("/paper/balance")
async def set_paper_balance(req: SetPaperBalanceReq):
    paper_trading.set_starting_balance(req.amount)
    live = manager.live_status()
    return paper_trading.get_balance(live.get("last_price"))


# --- Auto-trader ---------------------------------------------------------
class AutoTradeReq(BaseModel):
    enabled: Optional[bool] = None
    timeframe: Optional[str] = None
    notional_usd: Optional[float] = None
    sl_atr_mult: Optional[float] = None
    tp_atr_mult: Optional[float] = None


@api_router.get("/autotrade")
async def autotrade_status():
    return autotrader.status()


@api_router.put("/autotrade")
async def autotrade_update(req: AutoTradeReq):
    return autotrader.update(req.model_dump(exclude_none=True))


app.include_router(api_router)


@app.websocket("/api/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    await manager.register(ws)
    try:
        # send an immediate snapshot so the client shows a price instantly
        await ws.send_text(manager._snapshot_msg())
        while True:
            # keep the connection open; ignore any inbound messages
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.unregister(ws)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    logger.info("MIB-Trader starting — live-first market data")
    await manager.initial_load()