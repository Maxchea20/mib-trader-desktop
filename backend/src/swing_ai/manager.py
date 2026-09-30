"""SwingManager: the paper-mode orchestrator.  One `step()` per few seconds; it decides whether the AI is woken."""
import logging
import time
from typing import Any, Dict, List, Optional

from . import engine, events, market_state, paper, risk, store
from .config import DEFAULT, SwingConfig
from .source import DbSource

logger = logging.getLogger(__name__)


class SwingManager:
    def __init__(self, cfg: Optional[SwingConfig] = None, source=None, llm=None):
        self.cfg = cfg or SwingConfig()
        self.source = source or DbSource()
        self.llm = llm
        self.prev_summary: Optional[Dict[str, Any]] = None
        self.last_snapshot: Optional[Dict[str, Any]] = None
        self.last_snap_1m: Optional[int] = None
        self.last_entry_review = -1e18
        self.last_manage_review = -1e18
        self.last_ai_call = -1e18
        self.cooldown = events.Cooldown(self.cfg.event_cooldown_seconds)
        self.last_decision: Optional[Dict[str, Any]] = None
        self.status: Dict[str, Any] = {"state": "idle", "last_error": None, "last_review": None}

    # ------------------------------------------------------------------ paper updates
    def _advance_paper(self, now: float, live_price: Optional[float]) -> Optional[Dict[str, Any]]:
        pos = store.active_position()
        if not pos:
            return None
        for bar in self.source.closed("1m", 200, now):
            if int(bar["ts"]) <= int(pos["last_bar_ts"] or 0):
                continue
            pos = paper.process_bar(pos, bar, self.cfg)
            if pos["status"] in ("CLOSED", "EXPIRED", "CANCELLED"):
                return None
            store.update_position(pos["id"], last_bar_ts=int(bar["ts"]))
            pos = {**pos, "last_bar_ts": int(bar["ts"])}
        if live_price and pos["status"] == "OPEN":
            pos = paper.process_price(pos, float(live_price), now)
            if pos["status"] == "CLOSED":
                return None
        return pos

    def _position_view(self, pos: Dict[str, Any], price: float, now: float) -> Dict[str, Any]:
        d = 1 if pos["side"] == "LONG" else -1
        fill = pos["fill_price"] or pos["plan_entry"]
        rd = pos["risk_dist"] or 1.0
        return {"id": pos["id"], "status": pos["status"], "side": pos["side"], "entry": fill, "sl": pos["sl"],
                "initial_sl": pos["sl0"], "tp": pos["tp"], "invalidation_price": pos["invalidation_price"],
                "thesis": pos["thesis"], "invalidation": pos["invalidation"],
                "age_minutes": int((now - (pos["opened_ts"] or pos["created_ts"])) / 60),
                "unrealized_r": round(d * (price - fill) / rd, 3) if pos["status"] == "OPEN" else None,
                "mfe_r": round(pos["mfe_r"] or 0, 3), "mae_r": round(pos["mae_r"] or 0, 3)}

    # ------------------------------------------------------------------ main step
    def step(self, now: float, live_price: Optional[float], ticker: Optional[Dict[str, Any]] = None,
             connected: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {"now": int(now), "reviewed": None, "events": [], "decision": None}
        if not connected or not live_price:
            self.status["state"] = "paused (no live feed)"
            return out
        pos = self._advance_paper(now, live_price)

        bars = self.source.closed("1m", 2, now)
        last1m = int(bars[-1]["ts"]) if bars else None
        snap = self.last_snapshot
        if last1m is not None and last1m != self.last_snap_1m:
            snap = market_state.build_snapshot(self.source, now, live_price, ticker,
                                               position=self._position_view(pos, live_price, now) if pos else None)
            self.last_snap_1m = last1m
            if snap is not None:
                summ = events.summarize(snap)
                evs = events.detect(self.prev_summary, summ, self._position_view(pos, live_price, now) if pos and pos["status"] == "OPEN" else None)
                self.prev_summary = summ
                self.last_snapshot = snap
            else:
                evs = []
                self.status["state"] = "waiting for enough candle history"
        else:
            evs = []
        if snap is not None and pos and pos["status"] == "OPEN":            # tick-level invalidation checks
            evs += events.invalidation_events(self._position_view(pos, live_price, now), live_price,
                                              snap["timeframes"]["15m"]["atr"])
        fresh = [e for e in evs if self.cooldown.allow(e, now)]
        for e in fresh:
            store.add_event(int(now), e)
        out["events"] = [e["kind"] for e in fresh]
        if snap is None:
            return out

        urgent = next((e for e in fresh if e.get("urgent")), None)
        wake = urgent or (fresh[0] if fresh and now - self.last_ai_call >= self.cfg.min_seconds_between_ai_calls else None)
        if self.llm is None:
            self.status["state"] = "no LLM configured"
            return out
        if pos and pos["status"] == "OPEN":
            if urgent or wake or now - self.last_manage_review >= self.cfg.management_seconds:
                self.last_manage_review = self.last_ai_call = now
                out["reviewed"] = "MANAGE"
                out["decision"] = self._manage(snap, wake, pos, now, live_price, ticker)
        elif wake or now - self.last_entry_review >= self.cfg.heartbeat_seconds:
            self.last_entry_review = self.last_ai_call = now
            out["reviewed"] = "ENTRY"
            out["decision"] = self._entry(snap, wake, pos, now, live_price, ticker)
        self.status["state"] = "running"
        return out

    # ------------------------------------------------------------------ entry
    def _entry(self, snap, wake, pending, now, price, ticker):
        wake_name = (wake or {}).get("kind", "HEARTBEAT_15M")
        sid = store.add_snapshot(int(now), price, snap)
        dec, raw, err, ms = engine.review_entry(self.llm, snap, wake, self.last_decision)
        row = dict(ts=int(now), kind="ENTRY", wake=wake_name, price=price, snapshot_id=sid, raw=raw, model=self.cfg.model,
                   latency_ms=ms, error=err, valid=int(dec is not None))
        if dec is None:                                   # unparseable / failed call = NO_TRADE, never a guess
            row.update(decision="NO_TRADE", risk_ok=0, risk_reasons="ai error: " + str(err))
            store.add_decision(**row)
            self.status["last_error"] = err
            return {"decision": "NO_TRADE", "error": err}
        row.update(decision=dec.decision, confidence=dec.confidence, entry=dec.entry, sl=dec.sl, tp=dec.tp,
                   thesis=dec.thesis, invalidation=dec.invalidation)
        self.last_decision = dec.to_dict()
        result = {"decision": dec.decision, "confidence": dec.confidence}
        if dec.decision == "NO_TRADE":
            if pending and pending["status"] == "PENDING":
                paper.cancel(pending, "AI_NO_TRADE", now)
            row.update(risk_ok=1, risk_reasons="")
            store.add_decision(**row)
            return result
        ctx = {"now": now, "has_active": False, "connected": True, **store.day_stats(now)}
        rr = risk.validate_entry(dec, snap, self.cfg, ctx)
        row.update(risk_ok=int(rr.ok), risk_reasons="; ".join(rr.reasons))
        did = store.add_decision(**row)
        result.update(risk_ok=rr.ok, risk_reasons=rr.reasons)
        if rr.ok:
            if pending and pending["status"] == "PENDING":
                paper.cancel(pending, "REPLACED", now)
            q = ticker or {}
            result["position_id"] = paper.open_from_plan(rr.plan, dec.to_dict(), did, now, q.get("bid"), q.get("ask"))
            self.last_manage_review = now                  # the entry review counts as the first management review
        return result

    # ------------------------------------------------------------------ management
    def _manage(self, snap, wake, pos, now, price, ticker):
        view = self._position_view(pos, price, now)
        sid = store.add_snapshot(int(now), price, snap)
        md, raw, err, ms = engine.review_manage(self.llm, snap, wake, view)
        row = dict(ts=int(now), kind="MANAGE", wake=(wake or {}).get("kind", "MANAGEMENT_5M"), price=price, snapshot_id=sid,
                   raw=raw, model=self.cfg.model, latency_ms=ms, error=err, valid=int(md is not None), position_id=pos["id"])
        if md is None:                                    # a failed review never closes or changes anything
            row.update(decision="HOLD", risk_ok=0, risk_reasons="ai error: " + str(err))
            store.add_decision(**row)
            self.status["last_error"] = err
            return {"decision": "HOLD", "error": err}
        rr = risk.validate_manage(md, pos, price, snap["timeframes"]["15m"]["atr"], self.cfg)
        row.update(decision=md.action, confidence=md.confidence, sl=md.new_sl, thesis=f"[{md.thesis_status}] {md.reason}",
                   risk_ok=int(rr.ok), risk_reasons="; ".join(rr.reasons))
        store.add_decision(**row)
        result = {"decision": md.action, "thesis_status": md.thesis_status, "applied": False}
        if rr.ok and md.action == "MOVE_SL":
            store.update_position(pos["id"], sl=md.new_sl)
            result["applied"] = True
        elif md.action == "EXIT":
            q = ticker or {}
            px = (q.get("bid") if pos["side"] == "LONG" else q.get("ask")) or price
            paper.close_now(pos, float(px), "AI_EXIT", now)
            result["applied"] = True
        if md.reversal_candidate and not self.cfg.allow_reverse:
            result["reversal_ignored"] = True
        return result


def report() -> Dict[str, Any]:
    rows = store.positions()
    return {"performance": paper.performance(rows), "decisions": {
        k: sum(1 for d in store.decisions(5000) if d["decision"] == k and d["kind"] == "ENTRY")
        for k in ("LONG", "SHORT", "NO_TRADE")}}
