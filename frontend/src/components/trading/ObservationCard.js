import React from "react";
import { AGENT_META } from "../../lib/api";
import { fmt } from "../../lib/style";
import { chipStyle, cardGlow } from "../../lib/observationStyle";
import { firstNarrativeLine, parseEvidenceFacts } from "../../lib/evidenceFacts";
import { AgentMessageTicker } from "./AgentMessageTicker";

/**
 * ObservationCard — same underlying `agent` object AgentCard.js always
 * used (AgentResult-shaped: agent, direction, confidence, strength,
 * evidence, key_levels, valid, timeframe). No new backend fields.
 */
export const ObservationCard = ({ agent, onClick, dimmed = false }) => {
  const meta = AGENT_META[agent.agent] || { name: agent.agent, focus: "", num: "" };
  const chip = chipStyle(agent.direction);
  const narrative = firstNarrativeLine(agent.evidence);
  const facts = parseEvidenceFacts(agent.evidence, 4);

  return (
    <button
      onClick={() => onClick(agent)}
      data-testid={`observation-card-${agent.agent}`}
      className={`breathe-glow panel text-left p-4 flex flex-col gap-3 w-full transition-opacity ${
        !agent.valid ? "opacity-70" : ""
      } ${dimmed ? "opacity-55" : ""}`}
      style={cardGlow(agent.direction)}
    >
      <div className="flex items-center justify-between">
        <span className="font-head font-bold text-[15px] text-slate-50">{meta.name}</span>
        <span
          className="font-mono-t text-[10px] font-bold px-2 py-0.5 rounded-sm border"
          style={chip}
        >
          {agent.direction === "NEUTRAL" ? "NONE" : agent.direction}
        </span>
      </div>

      {narrative && (
        <div className="font-mono-t text-[12px] text-slate-100 leading-snug">{narrative}</div>
      )}

      {facts.length > 0 && (
        <div className="flex flex-col gap-1 pt-2 border-t border-[#1d2635]">
          {facts.map((f, i) => (
            <div key={i} className="flex justify-between font-mono-t text-[11px]">
              <span className="text-slate-500">{f.label}</span>
              <span className="text-slate-100 font-semibold">{f.value}</span>
            </div>
          ))}
        </div>
      )}

      {agent.key_levels?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {agent.key_levels.slice(0, 3).map((lv, i) => (
            <span key={i} className="font-mono-t text-[9px] px-1.5 py-0.5 rounded-sm bg-[#151b24] border border-[#1d2635] text-slate-400">
              {lv.label} ${fmt(lv.price, 0)}
            </span>
          ))}
        </div>
      )}

      {!agent.valid && (
        <div className="font-mono-t text-[9px] text-amber-500/80">low reliability</div>
      )}

      <div className="mt-auto pt-1">
        <AgentMessageTicker
          message={`${meta.name} · ${narrative || "observe() · " + agent.timeframe}`}
          accentColor={chip.color}
        />
      </div>
    </button>
  );
};