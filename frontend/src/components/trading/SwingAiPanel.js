import React, { useEffect, useState } from "react";
import { getSwingAiLatest, getSwingAiTrades, updateSwingAiSettings, getMexcAccount, getSwingAiModels, killSwingAi } from "@/lib/api";

/**
 * SwingAiPanel
 *
 * Displays the stored GPT analysis. Every analysis field below (daily / 4H / 1H / 15M views, structure, entry analysis,
 * decision, levels, confidence, thesis, invalidation, alerts) is read verbatim from the stored OpenAI response.
 * MIB computes no market opinion here: this component only formats text and numbers it was given.
 */

const px = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 1 }));
const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(Number(v) * 100)}%`);
const when = (ts) => (ts ? new Date(Number(ts) * 1000).toLocaleString() : "—");

const DECISION_COLOR = {
  LONG: { rgb: "0,245,155", label: "LONG" },
  SHORT: { rgb: "255,59,86", label: "SHORT" },
  NO_TRADE: { rgb: "148,163,184", label: "NO TRADE" },
};

const Line = ({ label, text, testId }) => (
  <div className="flex gap-3 py-1.5 border-b border-[#141c29] last:border-b-0" data-testid={testId}>
    <span className="widget-label w-20 shrink-0 pt-0.5">{label}</span>
    <span className="font-mono-t text-[12px] leading-snug text-slate-200">{text || "—"}</span>
  </div>
);

const Stat = ({ label, value, color }) => (
  <div className="flex flex-col">
    <span className="widget-label">{label}</span>
    <span className="font-mono-t text-sm" style={{ color: color || "#e2e8f0" }}>{value}</span>
  </div>
);


const REASON_TEXT = {
  TP: "Take-profit hit",
  SL: "Stop-loss hit",
  SL_MOVED: "Stop-loss hit (AI had moved it)",
  AI_INVALID: "AI closed it: its idea was no longer valid",
  AI_OPPOSITE: "AI closed it: a strong opposite signal appeared",
  AI_WEAKENING: "AI closed it: its idea was weakening",
  AI_EXIT_VALID: "AI closed it early (it still called the idea valid)",
  AI_EXIT: "AI closed it early",
  EXCHANGE_CLOSED: "Closed on the exchange",
  AI_NO_TRADE: "AI cancelled the order (changed its mind)",
  REPLACED: "AI replaced it with a new order",
  EXPIRED: "Order expired before price reached it",
};
const reasonText = (r) => REASON_TEXT[r] || r || "—";
const isActiveTrade = (t) => t.status === "OPEN" || t.status === "PENDING";
const NOT_FILLED = (t) => !t.fill_price && (t.status === "CANCELLED" || t.status === "EXPIRED");
const dur = (m) => {
  if (m === null || m === undefined) return "—";
  const mins = Math.round(Number(m));
  if (mins < 60) return `${Number(m).toFixed(mins < 10 ? 1 : 0)} min`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h}h ${mins % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
};
const money = (v) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `${v < 0 ? "-" : "+"}$${Math.abs(v).toFixed(2)}`);
const tradeMoney = (t) => {
  const risk = Number(t.qty) * Number(t.risk_dist);
  const gross = t.r_gross === null || t.r_gross === undefined ? null : Number(t.r_gross) * risk;
  const fees = Number(t.fees_usd || 0);
  const funding = Number(t.funding_usd || 0);
  return { gross, fees, funding, net: gross === null ? null : gross - fees - funding };
};

const TradeCard = ({ t }) => {
  const long = t.side === "LONG";
  const side = <span className={`px-1.5 py-0.5 rounded-sm text-[10px] border ${long ? "text-emerald-300 border-emerald-500/40" : "text-rose-300 border-rose-500/40"}`}>{t.side}</span>;
  const when_ = <span className="text-slate-500">{when(t.opened_ts || t.created_ts)}</span>;
  const why = (t.entry_headline || t.entry_thesis) && (
    <div className="mt-1.5 text-[10px] text-slate-500 leading-snug" data-testid="swing-trade-why">
      <div><span className="text-slate-400">Why GPT entered:</span> {t.entry_headline || t.entry_thesis}{t.entry_headline && t.entry_thesis ? ` — ${t.entry_thesis}` : ""}</div>
      {t.exit_note && (
        <div><span className="text-slate-400">What GPT said later</span> ({t.management_reviews} check{t.management_reviews === 1 ? "" : "s"}{t.exit_wake ? `, woken by ${t.exit_wake}` : ""}): {t.exit_note}</div>
      )}
      {t.if_held && (
        <div data-testid="swing-if-held"><span className="text-slate-400">If the AI had NOT closed it (original stop and target):</span>{" "}
          {t.if_held.status === "DONE" || t.if_held.status === "TIMEOUT"
            ? `it would have ended ${t.if_held.ended_by} at ${Number(t.if_held.r_net).toFixed(2)}R instead of ${Number(t.r_net).toFixed(2)}R — ${Number(t.r_net) >= Number(t.if_held.r_net) ? "closing early saved money" : "closing early cost money"}`
            : "still running, not decided yet"}
        </div>
      )}
    </div>
  );

  if (NOT_FILLED(t)) {
    return (
      <div className="py-2 border-t border-[#141c29] opacity-80" data-testid="swing-trade-card">
        <div className="flex items-center gap-2">{side}<span className="text-slate-300">Order never filled</span>{when_}</div>
        <div className="mt-1 text-slate-400">
          Waited to {long ? "buy" : "sell"} at <b className="text-slate-200">{px(t.plan_entry)}</b> (stop {px(t.sl)}, target {px(t.tp)}) — {reasonText(t.exit_reason)}. No money was made or lost.
        </div>
        {why}
      </div>
    );
  }
  if (t.status === "PENDING") {
    return (
      <div className="py-2 border-t border-[#141c29]" data-testid="swing-trade-card">
        <div className="flex items-center gap-2">{side}<span className="text-amber-300">Waiting for price</span>{when_}</div>
        <div className="mt-1 text-slate-300">
          Will {long ? "buy" : "sell"} at <b>{px(t.plan_entry)}</b> ({t.entry_type === "STOP" ? "when price breaks through" : "when price comes back to it"}). Stop {px(t.sl)} · Target {px(t.tp)}
        </div>
        {why}
      </div>
    );
  }
  if (t.status === "OPEN") {
    return (
      <div className="py-2 border-t border-[#141c29]" data-testid="swing-trade-card">
        <div className="flex items-center gap-2">{side}<span className="text-cyan-300">Trade is open</span>{when_}<span className="text-slate-500">· open for {dur(t.held_minutes)}</span></div>
        <div className="mt-1 text-slate-300">
          In at <b>{px(t.fill_price)}</b> · Stop {px(t.sl)} · Target {px(t.tp)}
        </div>
        <div className="mt-0.5 text-slate-500">Best so far {Number(t.mfe_r || 0).toFixed(2)}R · Worst dip {Number(t.mae_r || 0).toFixed(2)}R</div>
        {why}
      </div>
    );
  }
  const m = tradeMoney(t);
  const win = Number(t.r_net) > 0.05;
  const loss = Number(t.r_net) < -0.05;
  const color = win ? "text-emerald-400" : loss ? "text-rose-400" : "text-slate-300";
  return (
    <div className="py-2 border-t border-[#141c29]" data-testid="swing-trade-card">
      <div className="flex flex-wrap items-center gap-2">
        {side}
        <span className={`font-semibold ${color}`}>{win ? "WON" : loss ? "LOST" : "FLAT"} {money(m.net)} ({Number(t.r_net) >= 0 ? "+" : ""}{Number(t.r_net).toFixed(2)}R)</span>
        {when_}<span className="text-slate-500">· held {dur(t.held_minutes)}</span>
      </div>
      <div className="mt-1 text-slate-300">
        In <b>{px(t.fill_price)}</b> → Out <b>{px(t.exit_price)}</b> · Stop was {px(t.sl0 || t.sl)} · Target was {px(t.tp)}
      </div>
      <div className={`mt-0.5 ${String(t.exit_reason || "").startsWith("AI_") ? "text-amber-300" : "text-slate-400"}`}>Closed because: {reasonText(t.exit_reason)}</div>
      <div className="mt-0.5 text-slate-500">
        Price move {money(m.gross)} · Fees {money(-m.fees)}{m.funding ? ` · Funding ${money(-m.funding)}` : ""} = <span className={color}>{money(m.net)}</span>
        {" "}· Best it went {Number(t.mfe_r || 0).toFixed(2)}R · Worst dip {Number(t.mae_r || 0).toFixed(2)}R
      </div>
      {why}
    </div>
  );
};

export const SwingAiPanel = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [trades, setTrades] = useState([]);
  const [tab, setTab] = useState("open");
  const [acct, setAcct] = useState(null);
  const [riskInput, setRiskInput] = useState(1);
  const [notionalInput, setNotionalInput] = useState(50000);
  const [liveRiskInput, setLiveRiskInput] = useState(0.5);
  const [liveMaxInput, setLiveMaxInput] = useState(100);
  const [saveMsg, setSaveMsg] = useState(null);
  const [models, setModels] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [d, t] = await Promise.all([getSwingAiLatest(), getSwingAiTrades(60)]);
        if (alive) { setData(d); setTrades(t || []); setError(null); }
      } catch (e) {
        if (alive) setError("Swing AI endpoint not reachable");
      }
    };
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const st = data?.settings;
  const liveSelected = st?.mode === "LIVE";

  useEffect(() => {
    if (st?.risk_pct != null) setRiskInput(st.risk_pct);
    if (st?.max_position_usd != null) setNotionalInput(st.max_position_usd);
    if (st?.live_risk_pct != null) setLiveRiskInput(st.live_risk_pct);
    if (st?.live_max_usd != null) setLiveMaxInput(st.live_max_usd);
  }, [st?.risk_pct, st?.max_position_usd, st?.live_risk_pct, st?.live_max_usd]);

  useEffect(() => {                                   // models this OpenAI key can actually use
    let alive = true;
    getSwingAiModels().then((r) => { if (alive) setModels(r); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {                                   // MEXC account is only read while the live inputs are open
    if (!liveSelected) { setAcct(null); return undefined; }
    let alive = true;
    getMexcAccount().then((r) => { if (alive) setAcct(r); }).catch((e) => { if (alive) setAcct({ connected: false, error: e?.message || "request failed" }); });
    return () => { alive = false; };
  }, [liveSelected]);

  const save = async (payload) => {
    try {
      await updateSwingAiSettings(payload);
      setSaveMsg(null);
      setData(await getSwingAiLatest());
    } catch (e) {
      setSaveMsg(e?.response?.data?.detail || "could not save");
    }
  };

  const a = data?.last_analysis;
  const dec = a ? DECISION_COLOR[a.decision] || DECISION_COLOR.NO_TRADE : null;
  const trade = data?.active_trade;
  const mg = data?.latest_management;
  const an = data?.analytics;
  const overall = an?.overall || {};
  const freq = an?.frequency || {};

  return (
    <div className="panel p-4" data-testid="swing-ai-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="font-head font-bold text-slate-200 tracking-wide">SWING AI</span>
          <span className="widget-label">BTC/USDT</span>
          {data?.mode === "LIVE"
            ? <span className="font-mono-t text-[10px] px-1.5 py-0.5 rounded-sm border border-rose-500/60 text-rose-300 bg-rose-500/10" data-testid="swing-mode-badge">LIVE · REAL MONEY</span>
            : <span className="font-mono-t text-[10px] px-1.5 py-0.5 rounded-sm border border-amber-500/50 text-amber-300 bg-amber-500/10" data-testid="swing-mode-badge">PAPER</span>}
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono-t text-[10px] text-slate-500 mr-2">
            {data ? `${data.model} · ${data.enabled ? data.state?.state || "on" : "OFF"}` : "…"}
          </span>
          {st && (
            <>
              <button type="button" onClick={() => save({ enabled: !st.enabled })} data-testid="swing-toggle-enabled"
                className={`px-3 py-1.5 rounded-sm border font-mono-t text-[11px] ${st.enabled ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-300" : "border-[#1d2635] text-slate-500"}`}>
                AUTO-TRADE {st.enabled ? "ON" : "OFF"}
              </button>
              <button type="button" onClick={() => save({ mode: liveSelected ? "PAPER" : "LIVE" })} data-testid="swing-toggle-mode"
                className={`px-3 py-1.5 rounded-sm border font-mono-t text-[11px] ${liveSelected ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-emerald-500/60 bg-emerald-500/10 text-emerald-300"}`}>
                MODE {liveSelected ? "LIVE" : "PAPER"}
              </button>
            </>
          )}
        </div>
      </div>
      {saveMsg && <div className="font-mono-t text-[11px] text-rose-400 mb-2">{saveMsg}</div>}

      {liveSelected && st && (() => {
        const armed = !st.live_block_reason;
        const num = (v) => (acct?.connected ? Number(v).toFixed(2) : "—");
        const field = "px-2 py-1 bg-[#0d121b] border border-[#1d2635] text-slate-200 font-mono-t text-xs rounded-sm";
        return (
          <div className={`mb-3 rounded-sm border ${armed ? "border-rose-500/40" : "border-amber-500/30"} bg-[#0d1119]`} data-testid="swing-live-controls">
            <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-[#1d2635]">
              <div className="flex items-center gap-2 font-mono-t text-[11px]" data-testid="swing-live-notice">
                <span className={`inline-block w-2 h-2 rounded-full ${armed ? "bg-rose-500" : "bg-amber-400"}`} />
                <span className={`font-semibold tracking-wide ${armed ? "text-rose-300" : "text-amber-300"}`}>
                  {armed ? "LIVE ARMED · real orders on MEXC" : "LIVE NOT ARMED · still paper"}
                </span>
                {armed && <span className="text-slate-500">size ≤ ${st.live_max_usd} · risk {st.live_risk_pct}% · {st.max_leverage}x</span>}
              </div>
              <button type="button" data-testid="swing-kill"
                onClick={async () => {
                  if (!window.confirm("KILL: cancel Swing AI's open order, close its position at market, switch to PAPER and pause the AI?")) return;
                  try { const r = await killSwingAi(); setSaveMsg(`Killed. Order cancelled: ${r.cancelled_order ? "yes" : "no"}, position closed: ${r.flattened ? "yes" : "no"}. Check MEXC.`); setData(await getSwingAiLatest()); }
                  catch (e) { setSaveMsg("KILL FAILED - close it by hand on MEXC: " + (e?.response?.data?.detail || e?.message || "")); }
                }}
                className="px-2.5 py-1 rounded-sm border border-rose-500/60 text-rose-300 hover:bg-rose-500/10 font-mono-t text-[10px] tracking-wide">
                KILL
              </button>
            </div>
            {!armed && (
              <details className="px-3 py-1.5 border-b border-[#1d2635] font-mono-t text-[10px] text-slate-500">
                <summary className="cursor-pointer text-slate-400">Why is it not armed?</summary>
                <div className="mt-1 leading-snug">{st.live_block_reason}</div>
              </details>
            )}
            {acct && !acct.connected && (
              <div className="px-3 py-1.5 border-b border-[#1d2635] font-mono-t text-[11px] text-rose-300">MEXC account not connected: {acct.error || "unknown error"}</div>
            )}
            <div className="grid grid-cols-3 divide-x divide-[#1d2635] border-b border-[#1d2635]">
              <div className="px-3 py-2"><Stat label="EQUITY" value={`${num(acct?.equity)} USDT`} /></div>
              <div className="px-3 py-2"><Stat label="AVAILABLE" value={`${num(acct?.available_balance)} USDT`} /></div>
              <div className="px-3 py-2"><Stat label="UNREALIZED P&L" value={num(acct?.unrealized_pnl)} /></div>
            </div>
            <div className="flex flex-wrap items-end gap-x-6 gap-y-3 px-3 py-3">
              <label className="flex flex-col gap-1" title="LIVE only: % of your real available MEXC balance risked if the stop is hit. One contract is the smallest size, so very small balances may be refused.">
                <span className="widget-label">LIVE RISK PER TRADE %</span>
                <input type="number" min="0.05" max="5" step="0.05" value={liveRiskInput} onChange={(e) => setLiveRiskInput(e.target.value)}
                  onBlur={() => save({ live_risk_pct: Number(liveRiskInput) })} className={`${field} w-24`} data-testid="swing-live-risk" />
              </label>
              <div className="flex flex-col gap-1">
                <span className="widget-label">LEVERAGE (ISOLATED)</span>
                <div className="flex gap-1">
                  {[1, 2, 3, 5, 10].map((x) => (
                    <button key={x} type="button" onClick={() => save({ max_leverage: x })}
                      className={`w-9 py-1 border font-mono-t text-[10px] rounded-sm ${Number(st.max_leverage) === x ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1d2635] text-slate-400"}`}>{x}x</button>
                  ))}
                </div>
              </div>
              <label className="flex flex-col gap-1" title="LIVE only: the largest position (USD notional) the AI may open.">
                <span className="widget-label">LIVE MAX SIZE (USD)</span>
                <input type="number" min="5" step="5" value={liveMaxInput} onChange={(e) => setLiveMaxInput(e.target.value)}
                  onBlur={() => save({ live_max_usd: Number(liveMaxInput) })} className={`${field} w-28`} data-testid="swing-live-max" />
              </label>
              <label className="flex items-center gap-2 pb-1.5 font-mono-t text-[11px] text-slate-300 cursor-pointer"
                title="Off by default: confirm on MEXC first that a stop-loss attached to an unfilled limit order is kept. Breakout (STOP) entries are never sent live.">
                <input type="checkbox" checked={!!st.live_allow_limit} onChange={(e) => save({ live_allow_limit: e.target.checked })} data-testid="swing-live-allow-limit" />
                Allow LIMIT entries
              </label>
            </div>
            <details className="px-3 py-2 border-t border-[#1d2635] font-mono-t text-[10px] text-slate-500">
              <summary className="cursor-pointer text-slate-400">Paper sizing (used when the mode is PAPER)</summary>
              <div className="flex flex-wrap items-end gap-x-6 gap-y-2 mt-2">
                <label className="flex flex-col gap-1"><span className="widget-label">PAPER RISK %</span>
                  <input type="number" min="0.1" max="2" step="0.1" value={riskInput} onChange={(e) => setRiskInput(e.target.value)}
                    onBlur={() => save({ risk_pct: Number(riskInput) })} className={`${field} w-20`} /></label>
                <label className="flex flex-col gap-1"><span className="widget-label">PAPER MAX SIZE (USD)</span>
                  <input type="number" min="100" step="100" value={notionalInput} onChange={(e) => setNotionalInput(e.target.value)}
                    onBlur={() => save({ max_position_usd: Number(notionalInput) })} className={`${field} w-28`} /></label>
                <span className="pb-1.5">Isolated margin · GPT never sets size</span>
              </div>
            </details>
          </div>
        );
      })()}

      {error && <div className="font-mono-t text-[11px] text-rose-400">{error}</div>}
      {data && !data.enabled && (
        <div className="font-mono-t text-[11px] text-slate-500 mb-3">
          Swing AI is OFF: no AI reviews are running. Press AUTO-TRADE above to switch it ON (needs OPENAI_API_KEY in the backend .env).
        </div>
      )}
      {st && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 font-mono-t text-[10px] text-slate-400" data-testid="swing-cost-controls">
          <span className="widget-label">COST CONTROLS</span>
          <label className="flex items-center gap-1" title="OFF (paper only): every LONG/SHORT the AI proposes is simulated; only a stop or target on the wrong side of the entry is refused. RELAXED: no min reward/risk, cooldown or daily limits. STRICT: all rules. LIVE would always use STRICT.">Safety rules
            <select value={st.safety} onChange={(e) => save({ safety: e.target.value })} data-testid="swing-safety-select"
              className={`bg-[#0d121b] border rounded-sm px-1 py-0.5 ${st.safety === "OFF" ? "border-rose-500/50 text-rose-300" : st.safety === "RELAXED" ? "border-amber-500/50 text-amber-300" : "border-[#1d2635] text-slate-200"}`}>
              {["STRICT", "RELAXED", "OFF"].map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1">Model
            <select value={data?.model || ""} onChange={(e) => save({ model: e.target.value })} data-testid="swing-model-select"
              className="bg-[#0d121b] border border-[#1d2635] text-slate-200 rounded-sm px-1 py-0.5">
              {Array.from(new Set([...(models?.models?.length ? models.models : ["gpt-5.4-mini", "gpt-5.4"]), data?.model].filter(Boolean)))
                .map((o) => <option key={o} value={o}>{o}{models?.models?.length && !models.models.includes(o) ? " (no access)" : ""}</option>)}
            </select>
          </label>
          {models?.error && <span className="text-amber-300">models: {models.error}</span>}
          {[
            ["Reasoning", "reasoning", ["default", "low", "medium", "high"]],
            ["Context", "context", ["FULL", "COMPACT", "LEAN"]],
          ].map(([label, key, opts]) => (
            <label key={key} className="flex items-center gap-1">{label}
              <select value={st[key]} onChange={(e) => save({ [key]: e.target.value })}
                className="bg-[#0d121b] border border-[#1d2635] text-slate-200 rounded-sm px-1 py-0.5">
                {opts.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          ))}
          {[
            ["Review every", "heartbeat_minutes", [5, 15, 30, 60, 120], "min"],
            ["Trade check every", "management_minutes", [1, 5, 10, 15, 30], "min"],
          ].map(([label, key, opts, unit]) => (
            <label key={key} className="flex items-center gap-1">{label}
              <select value={Number(st[key])} onChange={(e) => save({ [key]: Number(e.target.value) })}
                className="bg-[#0d121b] border border-[#1d2635] text-slate-200 rounded-sm px-1 py-0.5">
                {opts.map((o) => <option key={o} value={o}>{o} {unit}</option>)}
              </select>
            </label>
          ))}
          {data?.analytics?.usage && (
            <span data-testid="swing-usage">
              tokens today {Number(data.analytics.usage.today.input_tokens + data.analytics.usage.today.output_tokens).toLocaleString()}
              {" "}(cached {Number(data.analytics.usage.today.cached_tokens).toLocaleString()})
              {data.analytics.usage.prices_set && data.analytics.usage.today.cost_usd !== null ? ` · ≈ $${Number(data.analytics.usage.today.cost_usd).toFixed(2)} today · $${Number(data.analytics.usage.all_time.cost_usd).toFixed(2)} total` : " · set SWING_AI_PRICE_IN / _OUT in .env for $"}
            </span>
          )}
        </div>
      )}

      {data?.enabled && !a && !data?.last_review && (
        <div className="font-mono-t text-[11px] text-slate-500 mb-3">Waiting for the first GPT analysis…</div>
      )}
      {data?.enabled && !a && data?.last_review && (
        <div className="font-mono-t text-[11px] text-rose-300 mb-3 whitespace-pre-wrap" data-testid="swing-last-failure">
          Last review at {when(data.last_review.ts)} ({data.last_review.wake_kind}) produced no valid analysis.
          {data.last_review.error ? ` Error: ${data.last_review.error}` : ` ${data.last_review.risk_reasons || ""}`}
        </div>
      )}
      {data?.state?.last_error && a && (
        <div className="font-mono-t text-[10px] text-amber-300 mb-2">Latest AI error: {data.state.last_error}</div>
      )}

      {a && (
        <div>
          <div className="flex flex-wrap items-start gap-x-8 gap-y-3 pb-3 border-b border-[#1d2635]">
            <div>
              <div className="widget-label mb-1">AI DECISION</div>
              <div className="font-head font-black text-4xl leading-none" style={{ color: `rgb(${dec.rgb})` }} data-testid="swing-decision">
                {dec.label}
              </div>
              <div className="font-mono-t text-[10px] text-slate-500 mt-1">{a.market_state || "—"}{a.entry_type ? ` · ${a.entry_type}` : ""}</div>
            </div>
            <div className="flex-1 min-w-[220px]">
              <div className="widget-label mb-1">HEADLINE</div>
              <div className="font-mono-t text-sm text-slate-100 leading-snug" data-testid="swing-headline">{a.headline || a.thesis || "—"}</div>
            </div>
            <div className="grid grid-cols-4 gap-5">
              <Stat label="Entry" value={px(a.entry)} />
              <Stat label="SL" value={px(a.sl)} color="#ff6b81" />
              <Stat label="TP" value={px(a.tp)} color="#34d399" />
              <Stat label="Confidence" value={pct(a.confidence)} />
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 mt-2">
            <div>
              <div className="widget-label mt-1">MARKET VIEW · GPT</div>
              <Line label="Daily" text={a.daily_analysis} testId="swing-daily" />
              <Line label="4H" text={a.h4_analysis} testId="swing-h4" />
              <Line label="1H" text={a.h1_analysis} testId="swing-h1" />
              <Line label="15M" text={a.m15_analysis} testId="swing-m15" />
            </div>
            <div>
              <div className="widget-label mt-1">READ · GPT</div>
              <Line label="Structure" text={a.structure_analysis} testId="swing-structure" />
              <Line label="Entry" text={a.entry_analysis} testId="swing-entry-analysis" />
              <Line label="Thesis" text={a.thesis} testId="swing-thesis" />
              <Line label="Invalidation" text={a.invalidation} testId="swing-invalidation" />
            </div>
          </div>

          <div className="flex flex-wrap gap-x-8 gap-y-3 mt-3">
            {a.wake_levels?.length > 0 && (
              <div className="min-w-[260px]">
                <div className="widget-label mb-1">AI ALERTS (its own wake levels)</div>
                {a.wake_levels.map((w, i) => (
                  <div key={i} className="font-mono-t text-[11px] text-slate-400">
                    {w.direction === "ABOVE" ? "▲" : "▼"} {px(w.price)} — {w.reason}
                  </div>
                ))}
              </div>
            )}
            {a.decision !== "NO_TRADE" && a.risk_ok === 0 && (
              <div className="font-mono-t text-[11px] text-amber-300" data-testid="swing-safety-reject">
                Safety layer rejected this proposal: {a.risk_reasons}
              </div>
            )}
            {trade && (
              <div className="p-2 rounded-sm border border-[#1d2635] bg-[#0d121b]" data-testid="swing-active-trade">
                <div className="widget-label mb-1">PAPER {trade.status === "PENDING" ? "ORDER (waiting for fill)" : "TRADE"}</div>
                <div className="font-mono-t text-[11px] text-slate-300">
                  {trade.side} {Number(trade.qty).toFixed(3)} BTC @ {px(trade.fill_price || trade.plan_entry)} · SL {px(trade.sl)} · TP {px(trade.tp)}
                </div>
                <div className="font-mono-t text-[10px] text-slate-500">MFE {Number(trade.mfe_r || 0).toFixed(2)}R · MAE {Number(trade.mae_r || 0).toFixed(2)}R</div>
              </div>
            )}
            {mg && (
              <div className="min-w-[260px]" data-testid="swing-management">
                <div className="widget-label mb-1">AI MANAGEMENT · {mg.decision} · {when(mg.ts)}</div>
                <div className="font-mono-t text-[11px] text-slate-300">{mg.thesis}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {a && (
        <div className="mt-3 pt-2 border-t border-[#1d2635] font-mono-t text-[10px] text-slate-500 flex flex-wrap gap-x-4 gap-y-1" data-testid="swing-last-analysis">
          <span>LAST ANALYSIS {when(a.ts)}</span>
          <span>wake: {a.wake_kind}{a.wake_detail ? ` — ${a.wake_detail}` : ""}</span>
          <span>price then {px(a.price)}</span>
          <span>snapshot #{a.snapshot_id}</span>
        </div>
      )}

      {data && (
        <div className="mt-3 pt-2 border-t border-[#1d2635]" data-testid="swing-trades">
          <div className="flex items-center gap-2 mb-2">
            <span className="widget-label">SWING AI TRADES</span>
            {["open", "history"].map((k) => {
              const n = trades.filter((t) => (k === "open" ? t.status === "OPEN" || t.status === "PENDING" : t.status !== "OPEN" && t.status !== "PENDING")).length;
              return (
                <button key={k} type="button" onClick={() => setTab(k)}
                  className={`font-mono-t text-[10px] px-2 py-0.5 rounded-sm border ${tab === k ? "bg-cyan-500/20 border-cyan-500/50 text-cyan-200" : "border-[#1d2635] text-slate-500"}`}>
                  {k === "open" ? `Open / pending (${n})` : `History (${n})`}
                </button>
              );
            })}
          </div>
          <div className="font-mono-t text-[11px]" data-testid="swing-trade-list">
            {trades.filter((t) => (tab === "open" ? isActiveTrade(t) : !isActiveTrade(t))).slice(0, 20).map((t) => <TradeCard key={t.id} t={t} />)}
            {trades.filter((t) => (tab === "open" ? isActiveTrade(t) : !isActiveTrade(t))).length === 0 && (
              <div className="py-3 text-center text-slate-600">{tab === "open" ? "No open or pending Swing AI orders" : "No finished Swing AI trades yet"}</div>
            )}
            <div className="pt-2 text-[10px] text-slate-600">R = multiples of the money risked on the trade (1R = the loss if the stop is hit). Net = after fees{tab === "history" ? " and funding" : ""}.</div>
          </div>
        </div>
      )}

      {an && (
        <div className="mt-3 pt-2 border-t border-[#1d2635] grid grid-cols-2 md:grid-cols-7 gap-3" data-testid="swing-performance">
          <Stat label="Closed trades" value={overall.trades ?? 0} />
          <Stat label="Win rate" value={overall.trades ? pct(overall.win_rate) : "—"} />
          <Stat label="Net R (fees in)" value={overall.trades ? Number(overall.net_r).toFixed(2) : "—"} />
          <Stat label="Expectancy R" value={overall.trades ? Number(overall.expectancy_r).toFixed(3) : "—"} />
          <Stat label="NO TRADE share" value={freq.no_trade_share === null || freq.no_trade_share === undefined ? "—" : pct(freq.no_trade_share)} />
          <Stat label="L / S trades" value={`${an.by_side?.LONG?.trades || 0} / ${an.by_side?.SHORT?.trades || 0}`} />
          <Stat label="AI exits: R saved vs holding"
            value={an.ai_exit_value?.judged ? `${Number(an.ai_exit_value.avg_r_saved_by_exiting).toFixed(2)} (${an.ai_exit_value.exit_was_better} better / ${an.ai_exit_value.exit_was_worse} worse)` : "—"} />
        </div>
      )}
    </div>
  );
};
