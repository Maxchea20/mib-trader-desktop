from fastapi import FastAPI, APIRouter, Query, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
import os
import sys
import asyncio
import logging
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).parent


def _desktop_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "mib-trader"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mib-trader"
    return Path.home() / ".local" / "share" / "mib-trader"


# Dev: backend/.env. Packaged desktop: data-folder .env (tray → Show Data Folder).
# First file to set a key wins (override=False).
load_dotenv(_desktop_data_dir() / ".env", override=False)
load_dotenv(ROOT_DIR / ".env", override=False)

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
from src import backtest_process_runner
from src import decision_log
from src import walkforward_log
from src import paper_trading
from src import autotrader
from src import ai_thesis
from src.market_data import mexc_private
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


@api_router.get("/analysis")
async def analysis(timeframe: str = Query("15m")):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe", "timeframes": TIMEFRAMES}
    return analysis_service.full_analysis(timeframe)


@api_router.get("/agents/{agent_id}")
async def agent_detail(agent_id: str, timeframe: str = Query("15m")):
    return analysis_service.single_agent(agent_id, timeframe)


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


@api_router.get("/backtest")
async def backtest(timeframe: str = Query("15m"), lookback: int = Query(150),
                   forward: int = Query(8), step: int = Query(3)):
    if timeframe not in TIMEFRAMES:
        return {"error": "invalid_timeframe", "timeframes": TIMEFRAMES}
    import asyncio
    result = await asyncio.to_thread(backtest_engine.run_backtest, timeframe,
                                     lookback, forward, step)
    if "summary" in result:
        try:
            backtest_log.record(timeframe, lookback, result["summary"])
        except Exception:
            pass
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


class WalkForwardRunReq(BaseModel):
    timeframe: str = "15m"
    days: float = 7.0
    start_balance: float = 250.0
    capital_pct: float = 10.0
    leverage: float = 5.0
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    weights: Optional[Dict[str, float]] = None
    htf_timeframes: Optional[List[str]] = None
    use_htf_gate: bool = True
    entry: Optional[Dict[str, float]] = None
    conflict: Optional[Dict[str, float]] = None
    htf_gate_values: Optional[Dict[str, float]] = None
    pivot_window_override: Optional[int] = None
    use_weather: bool = True
    use_hunt_lifecycle: bool = True


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
        weights = {
            k: max(runtime_settings.WEIGHT_MIN, min(runtime_settings.WEIGHT_MAX, float(v)))
            for k, v in weights.items()
        }

    def _merged_override(custom, live_getter):
        if not custom:
            return None
        base = dict(live_getter())
        base.update({k: float(v) for k, v in custom.items() if k in base})
        return base

    entry_override = _merged_override(req.entry, runtime_settings.entry)
    conflict_override = _merged_override(req.conflict, runtime_settings.conflict)
    htf_gate_override = _merged_override(req.htf_gate_values, runtime_settings.htf_gate)

    run_id = f"BT-{uuid.uuid4().hex[:10]}"
    params = dict(
        timeframe=req.timeframe, days=req.days, start_balance=req.start_balance,
        capital_pct=req.capital_pct, leverage=req.leverage,
        sl_atr_mult=req.sl_atr_mult, tp_atr_mult=req.tp_atr_mult,
        weights_override=weights, htf_timeframes=req.htf_timeframes,
        use_htf_gate=req.use_htf_gate, entry_override=entry_override,
        conflict_override=conflict_override, htf_gate_override=htf_gate_override,
        pivot_window_override=req.pivot_window_override,
        use_weather=req.use_weather,
        use_hunt_lifecycle=req.use_hunt_lifecycle,
    )
    backtest_process_runner.runner.start(run_id, params)
    return {"id": run_id, "status": "started"}


@api_router.get("/backtest/walkforward/status/{run_id}")
async def walkforward_status(run_id: str):
    status = backtest_process_runner.runner.get_status(run_id)
    if status is None:
        return {"error": "run_not_found"}
    return status


@api_router.get("/backtest/walkforward/result/{run_id}")
async def walkforward_result(run_id: str):
    result = backtest_process_runner.runner.get_result(run_id)
    if result is None:
        return {"error": "run_not_found"}
    if result.get("status") != "done":
        return result
    if not backtest_process_runner.runner.is_logged(run_id):
        try:
            walkforward_log.record(result)
            backtest_process_runner.runner.mark_logged(run_id)
        except Exception as e:
            print(f"[walkforward_log] FAILED to record run {run_id}: {type(e).__name__}: {e}")
    return result


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
    ok = backtest_process_runner.runner.stop(run_id)
    if not ok:
        return {"error": "run_not_found"}
    return {"ok": True}


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


class AutoTradeReq(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    timeframe: Optional[str] = None
    notional_usd: Optional[float] = None
    sl_atr_mult: Optional[float] = None
    tp_atr_mult: Optional[float] = None
    sizing_mode: Optional[str] = None
    allocation_pct: Optional[float] = None
    leverage: Optional[float] = None
    refresh_normal_base: Optional[bool] = None
    max_live_notional_usd: Optional[float] = None


@api_router.get("/autotrade")
async def autotrade_status():
    return await asyncio.to_thread(autotrader.status)


@api_router.get("/ai/thesis")
async def ai_thesis_status():
    return ai_thesis.status()


@api_router.get("/mexc/account")
async def mexc_account():
    """Real MEXC futures USDT balance/PnL — powers LiveTradingControls."""
    live_armed = os.environ.get("MEXC_LIVE_TRADING_ENABLED", "").lower() == "true"
    keys_present = mexc_private.keys_present()
    try:
        assets = await asyncio.to_thread(mexc_private.get_assets)
    except mexc_private.MexcPrivateError as e:
        hint = (
            "Desktop app: tray → Show Data Folder → put MEXC_API_KEY, MEXC_API_SECRET "
            "and MEXC_LIVE_TRADING_ENABLED=true in that .env → Restart Trading Engine. "
            "Dev: same three lines in backend/.env."
        )
        return {
            "connected": False,
            "error": str(e),
            "hint": hint,
            "live_armed": live_armed,
            "keys_present": keys_present,
        }
    except Exception as e:
        return {
            "connected": False,
            "error": f"unexpected error: {e}",
            "live_armed": live_armed,
            "keys_present": keys_present,
        }

    usdt = next((a for a in assets if a.get("currency") == "USDT"), None)
    if not usdt:
        return {
            "connected": True,
            "error": "No USDT asset entry in account",
            "assets": assets,
            "live_armed": live_armed,
            "keys_present": keys_present,
        }

    return {
        "connected": True,
        "currency": "USDT",
        "equity": float(usdt.get("equity") or 0),
        "available_balance": float(usdt.get("availableBalance") or 0),
        "cash_balance": float(usdt.get("cashBalance") or 0),
        "position_margin": float(usdt.get("positionMargin") or 0),
        "unrealized_pnl": float(usdt.get("unrealized") or 0),
        "live_armed": live_armed,
        "keys_present": keys_present,
    }


@api_router.put("/autotrade")
async def autotrade_update(req: AutoTradeReq):
    return await asyncio.to_thread(autotrader.update, req.model_dump(exclude_none=True))


@api_router.get("/decisions/stats")
async def decision_stats(since_hours: int = Query(24)):
    import time as _time
    since_ts = int(_time.time()) - since_hours * 3600
    return decision_log.logger.get_diagnostic_stats(since_ts)


@api_router.get("/decisions/recent")
async def decisions_recent(limit: int = Query(20)):
    with decision_log.db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"decisions": [dict(r) for r in rows]}


@api_router.get("/decisions/{decision_id}")
async def decision_detail(decision_id: str):
    with decision_log.db._conn() as conn:
        decision = conn.execute(
            "SELECT * FROM decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        if decision is None:
            return {"error": "decision_not_found"}
        agents = conn.execute(
            "SELECT * FROM agent_decisions WHERE decision_id=?", (decision_id,)
        ).fetchall()
    return {"decision": dict(decision), "agents": [dict(a) for a in agents]}


@api_router.get("/setups/{setup_id}")
async def setup_detail(setup_id: str):
    with decision_log.db._conn() as conn:
        setup = conn.execute("SELECT * FROM setups WHERE id=?", (setup_id,)).fetchone()
        if setup is None:
            return {"error": "setup_not_found"}
        checkpoints = conn.execute(
            "SELECT * FROM market_checkpoints WHERE setup_id=? ORDER BY timestamp", (setup_id,)
        ).fetchall()
        changes = conn.execute(
            "SELECT * FROM market_changes WHERE setup_id=? ORDER BY timestamp", (setup_id,)
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE setup_id=? ORDER BY timestamp", (setup_id,)
        ).fetchall()
    return {
        "setup": dict(setup),
        "checkpoints": [dict(c) for c in checkpoints],
        "changes": [dict(c) for c in changes],
        "decisions": [dict(d) for d in decisions],
    }


app.include_router(api_router)


@app.websocket("/api/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    await manager.register(ws)
    try:
        await ws.send_text(manager._snapshot_msg())
        while True:
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
    logger.info(
        "MEXC keys=%s live_armed=%s",
        bool(os.environ.get("MEXC_API_KEY")) and bool(os.environ.get("MEXC_API_SECRET")),
        os.environ.get("MEXC_LIVE_TRADING_ENABLED", "").lower() == "true",
    )
    await manager.initial_load()
    asyncio.create_task(ai_thesis.loop())
