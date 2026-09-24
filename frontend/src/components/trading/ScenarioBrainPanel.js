import React from "react";

const fmtTime = (ts) =>
  ts ? new Date(ts * 1000).toLocaleTimeString("en-US", { hour12: false }) : "—";
const fmtPrice = (v) => (v != null ? `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 1 })}` : "—");

export const ScenarioBrainPanel = ({ auto, syncStatus }) => {
  const connected = !!(auto?.state?.connected ?? syncStatus?.live?.connected);
  const result = auto?.state?.last_scenario_result || {};
  const tradeLog = auto?.state?.last_scenario_trade_log;

  const slot = result.m5_slot;
  const action = result.action;
  const setup = result.setup || (result.scenario === "FRESH_PULLBACK_CONTINUATION" ? "S2"
    : result.scenario === "FRESH_CLEAN_BREAKOUT" ? "S1" : null);
  const hasThesis = !!(result.thesis_id || result.direction || result.provisional || setup);

  let badge = "WAIT";
  let rgb = "148,163,184";
  if (!connected) { badge = "OFFLINE"; rgb = "71,85,105"; }
  else if (action === "FIRE") {
    badge = result.direction === "SHORT" ? "FIRE SHORT" : "FIRE LONG";
    rgb = result.direction === "SHORT" ? "255,59,86" : "0,245,155";
  } else if (action === "FIRE_FAILED") { badge = "FIRE FAILED"; rgb = "255,59,86"; }
  else if (action === "CANCEL") { badge = "CANCELLED"; rgb = "255,59,86"; }
  else if (action === "SKIPPED") { badge = "SKIPPED"; rgb = "148,163,184"; }
  else if (action === "ALREADY_ATTEMPTED") { badge = "HANDLED"; rgb = "100,116,139"; }
  else if (hasThesis && String(result.reason || "").includes("C watching")) {
    badge = "C WATCHING"; rgb = "251,191,36";
  } else if (hasThesis) { badge = "ARMED"; rgb = "251,191,36"; }
  else { badge = "WAIT"; rgb = "251,191,36"; }
  const color = `rgb(${rgb})`;

  let caseLabel = "NO ACTIVE THESIS";
  if (hasThesis) {
    const form = result.provisional ? "forming 15m" : "15m";
    if (setup === "S2") caseLabel = `S2 — pullback + fresh 5m · C on that 5m`;
    else if (slot === 3) caseLabel = `S1 slot 3 — outside early window`;
    else if (slot === 1 || slot === 2) caseLabel = `S1 slot ${slot} · ${form} · C on this 5m`;
    else caseLabel = `S1 · ${form} thesis · waiting slot 1 or 2`;
  }

  const whyLines = [];
  if (!connected) {
    whyLines.push("Market data connection is down — engine is not evaluating.");
  } else if (!hasThesis) {
    whyLines.push(result.reason || "No forming-15m CHoCH/BOS yet. 15m close is not the start gun.");
  } else {
    whyLines.push(`Thesis ${result.thesis_id || "—"}: ${result.direction || "—"}${result.origin_event ? ` (${result.origin_event})` : ""}${result.provisional ? " · still forming" : ""}`);
    if (slot === 1 || slot === 2) {
      whyLines.push(`M5 slot ${slot} printed. C watches that 5m's five 1m candles. First close across the line enters.`);
      if (action === "WAIT") whyLines.push("C watching — not locked to minute 1 or 3.");
      if (action === "FIRE") whyLines.push("C 1m close crossed the line — Isolated path.");
      if (action === "CANCEL") whyLines.push(result.reason || "C miss on this 5m. Thesis can still live.");
    } else {
      whyLines.push("Watching slot 1 and 2 of this same 15m. Slot 3 is late.");
    }
    if (setup === "S2") whyLines.push("S2: extension then pullback then a fresh 5m, then C.");
  }

  const showC = (slot === 1 || slot === 2) && (result.c_intended_ts != null || tradeLog?.c_intended_ts != null);

  return (
    <div className="breathe-glow panel p-4 relative overflow-hidden" style={{ ["--glow-rgb"]: rgb, borderColor: `rgba(${rgb},0.65)` }} data-testid="scenario-brain-hero">
      <div className="flex items-center justify-between">
        <span className="widget-label">Scenario Engine · S1 / S2 + C</span>
        <span className="widget-label">{connected ? "● live" : "○ offline"}</span>
      </div>
      <div className="flex items-end justify-between mt-2">
        <div>
          <div className="font-head font-black text-4xl leading-none pulse-dot" style={{ color, textShadow: `0 0 18px rgba(${rgb},0.7)` }} data-testid="scenario-decision-badge">{badge}</div>
          <div className="font-mono-t text-[11px] text-slate-400 mt-2">{caseLabel}</div>
        </div>
        {hasThesis && (
          <div className="text-right">
            <div className="widget-label">Thesis</div>
            <div className="font-mono-t font-bold text-sm text-cyan-400">{result.thesis_id || "ARMED"}</div>
            <div className="font-mono-t text-[10px] text-slate-500 mt-0.5">{result.direction || ""}{result.provisional ? " · forming" : ""}</div>
          </div>
        )}
      </div>
      {showC && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 pt-3 border-t border-[#1d2635] font-mono-t text-[11px]">
          <div><div className="widget-label">C 1m time</div><div className="text-slate-200 mt-0.5">{fmtTime(result.c_intended_ts ?? tradeLog?.c_intended_ts)}</div></div>
          <div><div className="widget-label">C price</div><div className="text-slate-200 mt-0.5">{fmtPrice(result.c_intended_price ?? tradeLog?.c_intended_price)}</div></div>
          {tradeLog?.actual_entry_price != null && (<div><div className="widget-label">Filled</div><div className="text-slate-200 mt-0.5">{fmtPrice(tradeLog.actual_entry_price)}</div></div>)}
          {tradeLog?.execution_delay_s != null && (<div><div className="widget-label">Delay</div><div className="text-slate-200 mt-0.5">{tradeLog.execution_delay_s}s</div></div>)}
        </div>
      )}
      <div className="mt-3 pt-3 border-t border-[#1d2635] space-y-1">
        {whyLines.map((line, i) => (<div key={i} className="font-mono-t text-[11px] text-slate-400">{line}</div>))}
      </div>
    </div>
  );
};
