import React, { useEffect, useState } from "react";
import { getSwingAiLatest, getSwingAiTrades } from "@/lib/api";

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

export const SwingAiPanel = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [trades, setTrades] = useState([]);
  const [tab, setTab] = useState("open");

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
          <span className="font-mono-t text-[10px] px-1.5 py-0.5 rounded-sm border border-amber-500/50 text-amber-300 bg-amber-500/10">PAPER</span>
        </div>
        <div className="font-mono-t text-[10px] text-slate-500">
          {data ? `${data.model} · ${data.enabled ? data.state?.state || "on" : "OFF"}` : "…"}
        </div>
      </div>

      {error && <div className="font-mono-t text-[11px] text-rose-400">{error}</div>}
      {data && !data.enabled && (
        <div className="font-mono-t text-[11px] text-slate-500 mb-3">
          Swing AI is off. Start the engine with SWING_AI_ENABLED=1 and OPENAI_API_KEY set (paper mode only).
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
          <div className="overflow-x-auto">
            <table className="w-full font-mono-t text-[11px] text-slate-300">
              <thead>
                <tr className="text-left text-slate-500 text-[10px]">
                  {["OPENED", "SIDE", "STATUS", "ENTRY", "SL", "TP", "EXIT", "WHY", "R (NET)", "MFE", "MAE", "FEES", "OUTCOME"].map((h) => (
                    <th key={h} className="pr-3 pb-1 font-normal">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades
                  .filter((t) => (tab === "open" ? t.status === "OPEN" || t.status === "PENDING" : t.status !== "OPEN" && t.status !== "PENDING"))
                  .slice(0, 20)
                  .map((t) => (
                    <tr key={t.id} className="border-t border-[#141c29]">
                      <td className="pr-3 py-1">{when(t.opened_ts || t.created_ts)}</td>
                      <td className={`pr-3 ${t.side === "LONG" ? "text-emerald-400" : "text-rose-400"}`}>{t.side}</td>
                      <td className="pr-3">{t.status}</td>
                      <td className="pr-3">{px(t.fill_price || t.plan_entry)}</td>
                      <td className="pr-3">{px(t.sl)}</td>
                      <td className="pr-3">{px(t.tp)}</td>
                      <td className="pr-3">{px(t.exit_price)}</td>
                      <td className="pr-3">{t.exit_reason || "—"}</td>
                      <td className={`pr-3 ${t.r_net > 0 ? "text-emerald-400" : t.r_net < 0 ? "text-rose-400" : ""}`}>
                        {t.r_net === null || t.r_net === undefined ? "—" : Number(t.r_net).toFixed(2)}
                      </td>
                      <td className="pr-3">{Number(t.mfe_r || 0).toFixed(2)}</td>
                      <td className="pr-3">{Number(t.mae_r || 0).toFixed(2)}</td>
                      <td className="pr-3">{t.fees_usd === null || t.fees_usd === undefined ? "—" : `$${Number(t.fees_usd).toFixed(2)}`}</td>
                      <td className="pr-3">{t.outcome || "—"}</td>
                    </tr>
                  ))}
                {trades.filter((t) => (tab === "open" ? t.status === "OPEN" || t.status === "PENDING" : t.status !== "OPEN" && t.status !== "PENDING")).length === 0 && (
                  <tr><td colSpan={13} className="py-3 text-center text-slate-600">{tab === "open" ? "No open or pending Swing AI orders" : "No finished Swing AI trades yet"}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {an && (
        <div className="mt-3 pt-2 border-t border-[#1d2635] grid grid-cols-2 md:grid-cols-6 gap-3" data-testid="swing-performance">
          <Stat label="Closed trades" value={overall.trades ?? 0} />
          <Stat label="Win rate" value={overall.trades ? pct(overall.win_rate) : "—"} />
          <Stat label="Net R (fees in)" value={overall.trades ? Number(overall.net_r).toFixed(2) : "—"} />
          <Stat label="Expectancy R" value={overall.trades ? Number(overall.expectancy_r).toFixed(3) : "—"} />
          <Stat label="NO TRADE share" value={freq.no_trade_share === null || freq.no_trade_share === undefined ? "—" : pct(freq.no_trade_share)} />
          <Stat label="L / S trades" value={`${an.by_side?.LONG?.trades || 0} / ${an.by_side?.SHORT?.trades || 0}`} />
        </div>
      )}
    </div>
  );
};
