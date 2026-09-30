import React, { useEffect, useState } from "react";
import { getSwingAiLatest } from "@/lib/api";

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

const Block = ({ title, text, testId }) => (
  <div className="mb-3" data-testid={testId}>
    <div className="widget-label mb-1">{title}</div>
    <div className="font-mono-t text-[11px] leading-relaxed text-slate-300 whitespace-pre-wrap">{text || "—"}</div>
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

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await getSwingAiLatest();
        if (alive) { setData(d); setError(null); }
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
      {data?.enabled && !a && (
        <div className="font-mono-t text-[11px] text-slate-500 mb-3">Waiting for the first GPT analysis…</div>
      )}

      {a && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-1">
            <div className="widget-label mb-1">MARKET VIEW <span className="text-slate-500">· GPT · {a.market_state || "—"}</span></div>
            <Block title="Daily" text={a.daily_analysis} testId="swing-daily" />
            <Block title="4H" text={a.h4_analysis} testId="swing-h4" />
            <Block title="1H" text={a.h1_analysis} testId="swing-h1" />
            <Block title="15M" text={a.m15_analysis} testId="swing-m15" />
          </div>

          <div className="lg:col-span-1">
            <Block title="STRUCTURE" text={a.structure_analysis} testId="swing-structure" />
            <Block title="ENTRY ANALYSIS" text={a.entry_analysis} testId="swing-entry-analysis" />
            <Block title="THESIS" text={a.thesis} testId="swing-thesis" />
            <Block title="INVALIDATION" text={a.invalidation} testId="swing-invalidation" />
          </div>

          <div className="lg:col-span-1">
            <div className="widget-label mb-1">AI DECISION</div>
            <div className="font-head font-black text-4xl leading-none" style={{ color: `rgb(${dec.rgb})` }} data-testid="swing-decision">
              {dec.label}
            </div>
            <div className="grid grid-cols-2 gap-3 mt-3">
              <Stat label="Entry" value={px(a.entry)} />
              <Stat label="Confidence" value={pct(a.confidence)} />
              <Stat label="SL" value={px(a.sl)} color="#ff6b81" />
              <Stat label="TP" value={px(a.tp)} color="#34d399" />
            </div>
            {a.entry_type && <div className="font-mono-t text-[10px] text-slate-500 mt-2">{a.entry_type} · invalidation price {px(a.invalidation_price)}</div>}
            {a.wake_levels?.length > 0 && (
              <div className="mt-3">
                <div className="widget-label mb-1">AI ALERTS (its own wake levels)</div>
                {a.wake_levels.map((w, i) => (
                  <div key={i} className="font-mono-t text-[10px] text-slate-400">
                    {w.direction} {px(w.price)} — {w.reason}
                  </div>
                ))}
              </div>
            )}
            {a.decision !== "NO_TRADE" && a.risk_ok === 0 && (
              <div className="mt-3 font-mono-t text-[10px] text-amber-300" data-testid="swing-safety-reject">
                Safety layer rejected this proposal: {a.risk_reasons}
              </div>
            )}
            {trade && (
              <div className="mt-3 p-2 rounded-sm border border-[#1d2635] bg-[#0d121b]" data-testid="swing-active-trade">
                <div className="widget-label mb-1">PAPER TRADE · {trade.status}</div>
                <div className="font-mono-t text-[11px] text-slate-300">
                  {trade.side} {trade.qty} BTC @ {px(trade.fill_price || trade.plan_entry)} · SL {px(trade.sl)} · TP {px(trade.tp)}
                </div>
                <div className="font-mono-t text-[10px] text-slate-500">MFE {Number(trade.mfe_r || 0).toFixed(2)}R · MAE {Number(trade.mae_r || 0).toFixed(2)}R</div>
              </div>
            )}
            {mg && (
              <div className="mt-3" data-testid="swing-management">
                <div className="widget-label mb-1">AI MANAGEMENT · {mg.decision} · {when(mg.ts)}</div>
                <div className="font-mono-t text-[11px] text-slate-300 whitespace-pre-wrap">{mg.thesis}</div>
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
