"""SwingManager: the paper-mode orchestrator.  MIB is the body: it moves data, stores what GPT says, enforces safety and
simulates fills.  GPT is the brain: every market interpretation comes from its reply."""
import json
import logging
from typing import Any, Dict, Optional

from . import analytics, engine, funding, live, paper, prompts, raw_data, risk, settings as sett, store
from .config import SwingConfig
from .schema import AI_EXIT_BY_STATUS
from .source import DbSource
from .wakes import WakeWatcher

logger = logging.getLogger(__name__)
SYMBOL = "BTC_USDT"


def _market(price: float, ticker: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    q = ticker or {}
    bid, ask = q.get("bid"), q.get("ask")
    return {"price": price, "bid": bid, "ask": ask,
            "spread_pct": round((ask - bid) / price * 100.0, 4) if bid and ask and price else None,
            "ticker_age_seconds": q.get("age_seconds")}


class SwingManager:
    def __init__(self, cfg: Optional[SwingConfig] = None, source=None, llm=None):
        self.cfg = cfg or SwingConfig()
        self.source = source or DbSource()
        self.llm = llm
        self.watch = WakeWatcher(self.cfg)
        funding.DEFAULT_RATE = self.cfg.funding_rate_8h
        self.last_entry_review = -1e18
        self.last_manage_review = -1e18
        self.last_ai_call = -1e18
        self.previous: Optional[Dict[str, Any]] = None       # GPT's own last analysis, fed back for continuity
        self._restored = False
        self._last_cf = -1e18
        self._flat_since: Dict[int, float] = {}
        self.status: Dict[str, Any] = {"state": "idle", "last_error": None}

    def _restore(self) -> None:
        self._restored = True
        row = store.latest_entry_decision()
        if row:
            self.previous = _previous_from_row(row)
        row = store.latest_decision()
        if row and row.get("wake_levels"):
            try:
                self.watch.set_ai_levels(json.loads(row["wake_levels"]))
            except Exception:
                pass

    def _prepare_llm(self) -> None:
        if hasattr(self.llm, "reasoning"):
            self.llm.reasoning = self.cfg.reasoning
        if hasattr(self.llm, "last_usage"):
            self.llm.last_usage = None

    def _usage(self) -> Dict[str, Any]:
        u = getattr(self.llm, "last_usage", None) or {}
        return {"input_tokens": u.get("input"), "cached_tokens": u.get("cached"), "output_tokens": u.get("output")}

    def _advance_counterfactuals(self, now: float) -> None:
        """'What if the AI had not exited?'  Observation only, about once a minute."""
        if now - self._last_cf < 60:
            return
        self._last_cf = now
        pending = store.open_counterfactuals()
        if not pending:
            return
        bars = self.source.closed("1m", 1000, now)
        for t in pending:
            paper.advance_counterfactual(t, bars, now)

    # ------------------------------------------------------------------ paper updates (accounting only)
    def _advance_paper(self, now: float, live_price: Optional[float]) -> Optional[Dict[str, Any]]:
        pos = store.active_trade()
        if not pos:
            return None
        lm = self._live_meta(pos)
        if lm and pos["status"] == "PENDING":             # a real resting order: only the exchange decides whether it filled
            return self._reconcile_live(pos, now, live_price)
        for bar in self.source.closed("1m", 200, now):
            if int(bar["ts"]) <= int(pos["last_bar_ts"] or 0):
                continue
            pos = paper.process_bar(pos, bar, self.cfg)
            if pos["status"] in ("CLOSED", "EXPIRED", "CANCELLED"):
                if lm and pos["status"] == "CLOSED":
                    live.flatten(pos["side"], lm["vol"])  # backstop: no-op when the exchange already closed it
                return None
            store.update_trade(pos["id"], last_bar_ts=int(bar["ts"]))
            pos = {**pos, "last_bar_ts": int(bar["ts"])}
        if live_price and pos["status"] == "OPEN":
            pos = paper.process_price(pos, float(live_price), now)
            if pos["status"] == "CLOSED":
                if lm:
                    live.flatten(pos["side"], lm["vol"])
                return None
            if lm:
                return self._reconcile_live(pos, now, live_price)
        return pos

    # ------------------------------------------------------------------ main step
    def step(self, now: float, live_price: Optional[float], ticker: Optional[Dict[str, Any]] = None,
             connected: bool = True, ai_enabled: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {"now": int(now), "reviewed": None, "wakes": [], "decision": None}
        if not connected or not live_price:
            self.status["state"] = "paused (no live feed)"
            return out
        if not self._restored:
            self._restore()
        pos = self._advance_paper(now, live_price)
        self._advance_counterfactuals(now)
        if not ai_enabled:                                # AI switched off: paper fills/stops still tracked, no AI calls
            self.status["state"] = "off (AI paused)"
            return out
        pview = raw_data.position_state(pos, live_price, now) if pos else None
        wakes = self.watch.check(now, live_price, self.source, pview if pos and pos["status"] == "OPEN" else None)
        for w in wakes:
            store.add_wake(int(now), w)
        out["wakes"] = [w["kind"] for w in wakes]
        if self.llm is None:
            self.status["state"] = "no LLM configured"
            return out
        urgent = next((w for w in wakes if w.get("urgent")), None)
        wake = urgent or (wakes[0] if wakes and now - self.last_ai_call >= self.cfg.min_seconds_between_ai_calls else None)
        is_open = bool(pos and pos["status"] == "OPEN")
        if is_open:
            due = bool(wake) or now - self.last_manage_review >= self.cfg.management_seconds
        else:
            due = bool(wake) or now - self.last_entry_review >= self.cfg.heartbeat_seconds
        if not due:
            self.status["state"] = "watching"
            return out
        snap = raw_data.build_raw_snapshot(self.source, now, live_price, ticker, pview, SYMBOL, self.cfg.context)
        if snap is None:
            self.status["state"] = "waiting for enough candle history"
            return out
        self.last_ai_call = now
        if is_open:
            self.last_manage_review = now
            out["reviewed"] = "MANAGE"
            out["decision"] = self._manage(snap, wake, pos, now, live_price, ticker)
        else:
            self.last_entry_review = now
            out["reviewed"] = "ENTRY"
            out["decision"] = self._entry(snap, wake, pos, now, live_price, ticker)
        self.status["state"] = "running"
        return out

    # ------------------------------------------------------------------ entry
    def _entry(self, snap, wake, pending, now, price, ticker):
        wk = wake or {"kind": "HEARTBEAT_15M", "detail": ""}
        sid = store.add_snapshot(int(now), SYMBOL, price, snap)
        self._prepare_llm()
        dec, raw, err, ms = engine.review_entry(self.llm, self.cfg, snap, wake, self.previous)
        row = dict(safety_mode=self.cfg.safety_mode, **self._usage(), ts=int(now), symbol=SYMBOL, kind="ENTRY", wake_kind=wk["kind"], wake_detail=wk.get("detail"), price=price,
                   snapshot_id=sid, model=self.cfg.model, prompt_version=prompts.PROMPT_VERSION, raw=raw, latency_ms=ms,
                   error=err, valid=int(dec is not None))
        if dec is None:                                   # unparseable / failed call = NO_TRADE, never a guess
            row.update(decision="NO_TRADE", risk_ok=0, risk_reasons="ai error: " + str(err))
            store.add_decision(**row)
            self.status["last_error"] = err
            return {"decision": "NO_TRADE", "error": err}
        row.update({k: getattr(dec, k) for k in ("decision", "confidence", "headline", "market_state", "daily_analysis", "h4_analysis", "h1_analysis",
                                                    "m15_analysis", "structure_analysis", "entry_analysis", "entry_type", "entry", "sl", "tp", "expected_hold_hours",
                                                    "thesis", "invalidation", "invalidation_price", "wake_levels")})
        self.previous = _previous_from_dec(dec)
        self.watch.set_ai_levels(dec.wake_levels)
        result = {"decision": dec.decision, "confidence": dec.confidence}
        if dec.decision == "NO_TRADE":
            if pending and pending["status"] == "PENDING":
                self._cancel_pending(pending, "AI_NO_TRADE", now)
            row.update(risk_ok=1, risk_reasons="")
            store.add_decision(**row)
            return result
        ctx = {"now": now, "has_active": False, "connected": True, **store.day_stats(now)}
        rr = risk.validate_entry(dec, _market(price, ticker), self.cfg, ctx)
        live_res = None
        if rr.ok and sett.effective_mode() == "LIVE":     # REAL ORDER: only when armed (see live.block_reason); any failure means no trade
            if pending and pending["status"] == "PENDING":
                self._cancel_pending(pending, "REPLACED", now)      # the old exchange order must go first, live.open_order refuses while one exists
                pending = None
            live_res = live.open_order(rr.plan, self.cfg)
            if not live_res["ok"]:
                rr = risk.RiskResult(False, ["live: " + str(live_res["error"])])
        row.update(risk_ok=int(rr.ok), risk_reasons="; ".join(rr.reasons))
        did = store.add_decision(**row)
        result.update(risk_ok=rr.ok, risk_reasons=rr.reasons)
        if rr.ok:
            if pending and pending["status"] == "PENDING":
                self._cancel_pending(pending, "REPLACED", now)
            q = ticker or {}
            plan = rr.plan
            if live_res:                                  # the real order's size and prices are the truth
                plan = {**plan, "qty": live_res["vol"] * live_res["contract_size"], "risk_usd": live_res["risk_usd"],
                        "entry": live_res["px"], "sl": live_res["sl"], "tp": live_res["tp"]}
            tid = paper.open_from_plan(plan, dec.to_dict(), did, now, q.get("bid"), q.get("ask"))
            if live_res:
                meta = json.loads((store.get_trade(tid) or {}).get("meta") or "{}")
                meta["live"] = {k: live_res[k] for k in ("order_id", "vol", "contract_size", "leverage")}
                store.update_trade(tid, meta=json.dumps(meta))
                if live_res.get("fill_price"):
                    store.update_trade(tid, fill_price=live_res["fill_price"], risk_dist=abs(live_res["fill_price"] - plan["sl"]))
                logger.warning("LIVE ORDER placed: %s %s vol=%s order_id=%s", plan["side"], plan["entry_type"], live_res["vol"], live_res["order_id"])
            result["trade_id"] = tid
            self.last_manage_review = now                  # the entry review counts as the first management review
        return result

    # ------------------------------------------------------------------ live helpers
    @staticmethod
    def _live_meta(t: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        try:
            return json.loads((t or {}).get("meta") or "{}").get("live")
        except Exception:
            return None

    def _cancel_pending(self, p: Dict[str, Any], reason: str, now: float) -> None:
        lm = self._live_meta(p)
        if lm:
            live.cancel_order(lm.get("order_id"))
        paper.cancel(p, reason, now)

    def _reconcile_live(self, pos: Dict[str, Any], now: float, price: Optional[float]) -> Optional[Dict[str, Any]]:
        """The exchange is the truth for a live trade.  Returns the updated trade, or None when it ended.  A failed read changes nothing."""
        lm = self._live_meta(pos)
        try:
            st = live.exchange_state(pos["side"], lm.get("order_id"))
        except Exception:
            logger.warning("live reconcile: could not read MEXC, leaving the trade as it is", exc_info=False)
            return pos
        if pos["status"] == "PENDING":
            if st["position_vol"] > 0:                    # filled on the exchange
                fill = st["avg_price"] or pos["plan_entry"]
                upd = dict(status="OPEN", fill_price=fill, opened_ts=int(now), risk_dist=abs(fill - pos["sl0"]), last_bar_ts=int(now // 60 * 60))
                store.update_trade(pos["id"], **upd)
                return {**pos, **upd}
            if not st["order_open"]:                      # gone without a fill
                paper.cancel(pos, "EXCHANGE_CANCELLED", now)
                return None
            if pos.get("expires_ts") and now > pos["expires_ts"]:
                live.cancel_order(lm.get("order_id"))
                paper.cancel(pos, "EXPIRED", now)
                return None
            return pos
        if st["position_vol"] <= 0:                       # open trade, flat on the exchange: closed there (stop, target or by hand)
            since = self._flat_since.setdefault(pos["id"], now)
            if now - since >= 20 and price:
                self._flat_since.pop(pos["id"], None)
                paper.close_now(pos, float(price), "EXCHANGE_CLOSED", now)
                return None
        else:
            self._flat_since.pop(pos["id"], None)
        return pos

    # ------------------------------------------------------------------ management
    def _manage(self, snap, wake, pos, now, price, ticker):
        wk = wake or {"kind": "MANAGEMENT_5M", "detail": ""}
        sid = store.add_snapshot(int(now), SYMBOL, price, snap)
        self._prepare_llm()
        md, raw, err, ms = engine.review_manage(self.llm, self.cfg, snap, wake)
        row = dict(safety_mode=self.cfg.safety_mode, **self._usage(), ts=int(now), symbol=SYMBOL, kind="MANAGE", wake_kind=wk["kind"], wake_detail=wk.get("detail"), price=price,
                   snapshot_id=sid, model=self.cfg.model, prompt_version=prompts.PROMPT_VERSION, raw=raw, latency_ms=ms,
                   error=err, valid=int(md is not None), trade_id=pos["id"])
        if md is None:                                    # a failed review never closes or changes anything
            row.update(decision="HOLD", risk_ok=0, risk_reasons="ai error: " + str(err))
            store.add_decision(**row)
            self.status["last_error"] = err
            return {"decision": "HOLD", "error": err}
        self.watch.set_ai_levels(md.wake_levels)
        rr = risk.validate_manage(md, pos, price, self.cfg)
        row.update(decision=md.action, confidence=md.confidence, sl=md.new_sl, thesis=f"[{md.thesis_status}] {md.reason}",
                   wake_levels=md.wake_levels, risk_ok=int(rr.ok), risk_reasons="; ".join(rr.reasons))
        store.add_decision(**row)
        result = {"decision": md.action, "thesis_status": md.thesis_status, "applied": False}
        lm = self._live_meta(pos)
        if rr.ok and md.action == "MOVE_SL":
            if lm:                                        # the exchange stop cannot be changed through the existing client: do not let paper and MEXC diverge
                result["live_note"] = "stop move not applied: live stops stay as placed"
            else:
                store.update_trade(pos["id"], sl=md.new_sl)
                result["applied"] = True
        elif md.action == "EXIT":
            q = ticker or {}
            px = (q.get("bid") if pos["side"] == "LONG" else q.get("ask")) or price
            if lm:
                live.flatten(pos["side"], lm["vol"])      # real market close first
            paper.close_now(pos, float(px), AI_EXIT_BY_STATUS.get(md.thesis_status, "AI_EXIT"), now)
            result["applied"] = True
        if md.reversal_candidate and not self.cfg.allow_reverse:
            result["reversal_ignored"] = True
        return result


_PREV_KEYS = ("decision", "confidence", "market_state", "daily_analysis", "h4_analysis", "h1_analysis", "m15_analysis",
              "structure_analysis", "entry_analysis", "entry", "sl", "tp", "thesis", "invalidation")


def _previous_from_dec(dec) -> Dict[str, Any]:
    d = dec.to_dict()
    return {k: d[k] for k in _PREV_KEYS}


def _previous_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: row.get(k) for k in _PREV_KEYS}


def report() -> Dict[str, Any]:
    return analytics.report()
