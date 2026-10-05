import React, { useEffect, useState } from "react";
import { getSwingAiLatest } from "@/lib/api";

/**
 * SwingAiHero - the top-right hero card. Every value is read from the stored GPT response (via /api/swing-ai/latest)
 * or from the paper-trade record. MIB computes no market opinion here.
 */

const px = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 1 }));
const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(Number(v) * 100)}%`);
const when = (ts) => (ts ? new Date(Number(ts) * 1000).toLocaleString() : "—");

const COLORS = {
  LONG: { rgb: "0,245,155", label: "LONG" },
  SHORT: { rgb: "255,59,86", label: "SHORT" },
  NO_TRADE: { rgb: "251,191,36", label: "NO TRADE" },
};

export const SwingAiHero = () => {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await getSwingAiLatest();
        if (alive) { setData(d); setFailed(false); }
      } catch (e) {
        if (alive) setFailed(true);
      }
    };
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const a = data?.last_analysis;
  const trade = data?.active_trade;
  const on = Boolean(data?.enabled);
  const c = a ? COLORS[a.decision] || COLORS.NO_TRADE : null;
  const rgb = !on || !a ? "71,85,105" : c.rgb;

  let tradeLine = "Paper FLAT";
  if (trade) {
    tradeLine = trade.status === "PENDING"
      ? `Paper ${trade.side} order waiting @ ${px(trade.plan_entry)}`
      : `Paper ${trade.side} OPEN @ ${px(trade.fill_price)}`;
  }

  return (
    <div className="breathe-glow panel p-4 relative overflow-hidden" style={{ ["--glow-rgb"]: rgb, borderColor: `rgba(${rgb},0.65)` }} data-testid="swing-ai-hero">
      <div className="flex items-center justify-between">
        <span className="widget-label">Swing AI</span>
        <span className="widget-label">{data ? `${data.model} · PAPER` : "GPT · PAPER"}</span>
      </div>

      <div className="flex items-center justify-between mt-2 font-mono-t text-[10px]">
        <span className={on ? "text-emerald-400" : "text-slate-500"}>{failed ? "○ Swing AI not reachable" : on ? "● AI is on" : "○ AI is off"}</span>
        <span className="text-slate-400">{tradeLine}</span>
      </div>

      <div className="flex items-end justify-between mt-3">
        <div>
          <div className="font-head font-black text-5xl leading-none text-breathe" style={{ color: `rgb(${rgb})`, ["--glow-rgb"]: rgb }} data-testid="swing-hero-decision">
            {a ? c.label : on ? "…" : "OFF"}
          </div>
          <div className="font-mono-t text-xs text-slate-400 mt-2">{a ? (a.headline || a.thesis || "") : on ? "waiting for the first GPT analysis" : "switch AUTO-TRADE on in the Swing AI panel"}</div>
        </div>
        <div className="text-right">
          <div className="widget-label">MARKET STATE (GPT)</div>
          <div className="font-head font-bold text-xl text-cyan-300">{a?.market_state || "—"}</div>
          <div className="widget-label mt-1">{a ? `confidence ${pct(a.confidence)}` : ""}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4 mt-4 pt-3 border-t border-[#1d2635] font-mono-t">
        <div><div className="widget-label">ENTRY</div><div className="text-slate-100">{px(a?.entry)}</div></div>
        <div><div className="widget-label">SL</div><div className="text-rose-300">{px(a?.sl)}</div></div>
        <div><div className="widget-label">TP</div><div className="text-emerald-300">{px(a?.tp)}</div></div>
      </div>

      {a && (
        <div className="mt-3 font-mono-t text-[10px] text-slate-500">
          last analysis {when(a.ts)} · wake: {a.wake_kind}
        </div>
      )}
      {data?.state?.last_error && <div className="mt-1 font-mono-t text-[10px] text-amber-300">AI error: {data.state.last_error}</div>}
    </div>
  );
};
