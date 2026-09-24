import React from "react";

const fmtTime = (ts) =>
  ts ? new Date(ts * 1000).toLocaleTimeString("en-US", { hour12: false }) : "—";
const fmtPrice = (v) => (v != null ? `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 1 })}` : "—");

/**
 * The decision hero panel. The Scenario Engine is the only entry engine
 * (the legacy Hunt C-FI hero and its FIRE badge were removed).
 *
 * Everything below is read directly from auto.state.last_scenario_result
 * and auto.state.last_scenario_trade_log, both already produced by
 * scenario_live_bridge.py and polled via the existing /autotrade
 * endpoint -- no new backend fields, no hardcoded labels.
 */
export const ScenarioBrainPanel = ({ auto, syncStatus }) => {
  // Real connectivity lives in market_data.manager's own STATE dict,
  // exposed via /market/sync-status -> live.connected -- NOT in
  // autotrader_state.py's STATE (which has no "connected" field at
  // all). Same fallback SystemHealthPanel.js already uses correctly.
  const connected = !!(auto?.state?.connected ?? syncStatus?.live?.connected);
  const result = auto?.state?.last_scenario_result;
  const tradeLog = auto?.state?.last_scenario_trade_log;

  const slot = result?.m5_slot;
  const action = result?.action;
  const hasThesis = slot != null;

  let badge = "WAIT";
  let rgb = "148,163,184";
  if (!connected) { badge = "OFFLINE"; rgb = "71,85,105"; }
  else if (!hasThesis) { badge = "WAIT"; rgb = "251,191,36"; }
  else if (slot === 2 && action === "WAIT") { badge = "WATCHING"; rgb = "251,191,36"; }
  else if (slot === 2 && action === "CANCEL") { badge = "CANCELLED"; rgb = "255,59,86"; }
  else if (action === "FIRE") { badge = result?.direction === "SHORT" ? "FIRE SHORT" : "FIRE LONG"; rgb = result?.direction === "SHORT" ? "255,59,86" : "0,245,155"; }
  else if (action === "ALREADY_ATTEMPTED") { badge = "HANDLED"; rgb = "100,116,139"; }
  const color = `rgb(${rgb})`;

  const caseLabel = !hasThesis ? "NO ACTIVE SETUP"
    : slot === 1 ? "M5#1 — IMMEDIATE"
    : slot === 3 ? "M5#3 — STANDARD"
    : "CASE 1 — M5#2";

  // Human-readable "why" lines (item 6) -- built from the same fields
  // the status badge above already used, not invented separately.
  const whyLines = [];
  if (!connected) {
    whyLines.push("Market data connection is down — engine is not evaluating.");
  } else if (!hasThesis) {
    whyLines.push(result?.reason || "No M15 structural break has produced a valid thesis yet.");
  } else {
    whyLines.push(`M15 thesis: ${result?.direction || "—"}${tradeLog?.origin_event ? ` (${tradeLog.origin_event})` : ""}`);
    whyLines.push(`M5 confirmation on slot #${slot}${slot === 2 ? " (Case-1 eligible)" : ""}`);
    if (slot === 2) {
      if (action === "WAIT") whyLines.push("Waiting for the first qualifying M1 candle CLOSE.");
      else if (action === "CANCEL") whyLines.push(`C watcher cancelled — ${result?.reason || "reason not reported"}`);
      else if (action === "FIRE") whyLines.push("M1 close crossed the structural level — entry submitted.");
    } else if (action === "FIRE") {
      whyLines.push(slot === 1 ? "Fired immediately — no C timing applies to M5#1." : "Standard scenario entry — no C timing applies to M5#3.");
    }
  }

  return (
    <div
      className="breathe-glow panel p-4 relative overflow-hidden"
      style={{ ["--glow-rgb"]: rgb, borderColor: `rgba(${rgb},0.65)` }}
      data-testid="scenario-brain-hero"
    >
      <div className="flex items-center justify-between">
        <span className="widget-label">Scenario Engine</span>
        <span className="widget-label">{connected ? "● live" : "○ offline"}</span>
      </div>

      <div className="flex items-end justify-between mt-2">
        <div>
          <div
            className="font-head font-black text-4xl leading-none pulse-dot"
            style={{ color, textShadow: `0 0 18px rgba(${rgb},0.7)` }}
            data-testid="scenario-decision-badge"
          >
            {badge}
          </div>
          <div className="font-mono-t text-[11px] text-slate-400 mt-2">{caseLabel}</div>
        </div>
        {hasThesis && (
          <div className="text-right">
            <div className="widget-label">Thesis</div>
            <div className="font-mono-t font-bold text-sm text-cyan-400">{result?.thesis_id || "—"}</div>
          </div>
        )}
      </div>

      {/* C entry details (item 3) -- only when relevant */}
      {slot === 2 && (result?.c_intended_ts != null || tradeLog?.c_intended_ts != null) && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 pt-3 border-t border-[#1d2635] font-mono-t text-[11px]">
          <div>
            <div className="widget-label">M1 confirmation</div>
            <div className="text-slate-200 mt-0.5">{fmtTime(result?.c_intended_ts ?? tradeLog?.c_intended_ts)}</div>
          </div>
          <div>
            <div className="widget-label">C intended price</div>
            <div className="text-slate-200 mt-0.5">{fmtPrice(result?.c_intended_price ?? tradeLog?.c_intended_price)}</div>
          </div>
          {tradeLog?.actual_entry_price != null && (
            <div>
              <div className="widget-label">Actual execution</div>
              <div className="text-slate-200 mt-0.5">{fmtPrice(tradeLog.actual_entry_price)}</div>
            </div>
          )}
          {tradeLog?.execution_delay_s != null && (
            <div>
              <div className="widget-label">Execution delay</div>
              <div className="text-slate-200 mt-0.5">{tradeLog.execution_delay_s}s</div>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 pt-3 border-t border-[#1d2635] space-y-1">
        {whyLines.map((line, i) => (
          <div key={i} className="font-mono-t text-[11px] text-slate-400">{line}</div>
        ))}
      </div>
    </div>
  );
};