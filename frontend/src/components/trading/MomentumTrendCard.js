import React from "react";
import { chipStyle, cardGlow } from "../../lib/observationStyle";
import { firstNarrativeLine, parseEvidenceFacts } from "../../lib/evidenceFacts";
import { AgentMessageTicker } from "./AgentMessageTicker";

/**
 * MomentumTrendCard — merges the momentum and trend AgentResult objects
 * into one card, matching the approved mockup's "Momentum & trend"
 * layout. Same real fields as everywhere else in this redesign; no new
 * backend data.
 */
export const MomentumTrendCard = ({ momentum, trend, onClick }) => {
  const primary = momentum || trend;
  const chip = chipStyle(primary?.direction);
  const momentumNarrative = momentum ? firstNarrativeLine(momentum.evidence) : null;
  const momentumFacts = momentum ? parseEvidenceFacts(momentum.evidence, 3) : [];
  const trendFacts = trend ? parseEvidenceFacts(trend.evidence, 2) : [];

  return (
    <button
      onClick={() => onClick(primary)}
      data-testid="observation-card-momentum-trend"
      className="breathe-glow panel text-left p-4 flex flex-col gap-3 w-full"
      style={cardGlow(primary?.direction)}
    >
      <div className="flex items-center justify-between">
        <span className="font-head font-bold text-[15px] text-slate-50">Momentum &amp; trend</span>
        <span className="font-mono-t text-[10px] font-bold px-2 py-0.5 rounded-sm border" style={chip}>
          {primary?.direction === "NEUTRAL" ? "NONE" : primary?.direction}
        </span>
      </div>

      {momentumNarrative && (
        <div className="font-mono-t text-[12px] text-slate-100 leading-snug">{momentumNarrative}</div>
      )}

      {momentumFacts.length > 0 && (
        <div className="flex flex-col gap-1 pt-2 border-t border-[#1d2635]">
          {momentumFacts.map((f, i) => (
            <div key={i} className="flex justify-between font-mono-t text-[11px]">
              <span className="text-slate-500">{f.label}</span>
              <span className="text-slate-100 font-semibold">{f.value}</span>
            </div>
          ))}
        </div>
      )}

      {trendFacts.length > 0 && (
        <div className="flex flex-col gap-1 pt-2 border-t border-[#1d2635]">
          {trendFacts.map((f, i) => (
            <div key={i} className="flex justify-between font-mono-t text-[11px]">
              <span className="text-slate-500">{f.label}</span>
              <span className="text-slate-100 font-semibold">{f.value}</span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-auto pt-1">
        <AgentMessageTicker
          message={`Momentum & trend · ${momentumNarrative || trendFacts[0]?.label || "observe()"}`}
          accentColor={chip.color}
        />
      </div>
    </button>
  );
};