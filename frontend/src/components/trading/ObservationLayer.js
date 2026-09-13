import React, { useState } from "react";
import { X } from "lucide-react";
import { ObservationCard } from "./ObservationCard";
import { LocationCard } from "./LocationCard";
import { MomentumTrendCard } from "./MomentumTrendCard";
import { AGENT_META } from "../../lib/api";
import { dirStyle, fmt } from "../../lib/style";

const AgentDetailModal = ({ agent, onClose }) => {
  if (!agent) return null;
  const meta = AGENT_META[agent.agent] || {};
  const st = dirStyle(agent.direction);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div
        className="panel max-w-lg w-full p-5 fade-up"
        style={{ boxShadow: st.glow }}
        onClick={(e) => e.stopPropagation()}
        data-testid="agent-detail-modal"
      >
        <div className="flex items-start justify-between">
          <div>
            <div className="widget-label">Agent {meta.num}</div>
            <div className="font-head font-bold text-2xl text-white">{meta.name}</div>
            <div className="font-mono-t text-[10px] text-slate-500">{meta.focus}</div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>

        <div className="grid grid-cols-3 gap-3 mt-4">
          <div className="panel p-2">
            <div className="widget-label">Direction</div>
            <div className={`font-head font-bold text-lg ${st.text}`}>{agent.direction}</div>
          </div>
          <div className="panel p-2">
            <div className="widget-label">Confidence</div>
            <div className="font-mono-t font-bold text-lg text-slate-100">{fmt(agent.confidence, 0)}%</div>
          </div>
          <div className="panel p-2">
            <div className="widget-label">Strength</div>
            <div className="font-mono-t font-bold text-lg text-cyan-400">{fmt(agent.strength, 0)}</div>
          </div>
        </div>

        <div className="mt-4">
          <div className="widget-label mb-1.5">Full evidence</div>
          <div className="space-y-1">
            {(agent.evidence || []).map((e, i) => (
              <div key={i} className="font-mono-t text-[11px] text-slate-300 flex gap-2">
                <span className="text-slate-600">·</span>{e}
              </div>
            ))}
          </div>
        </div>

        {agent.key_levels?.length > 0 && (
          <div className="mt-4">
            <div className="widget-label mb-1.5">Key levels</div>
            <div className="flex flex-wrap gap-1.5">
              {agent.key_levels.map((lv, i) => (
                <span key={i} className="font-mono-t text-[10px] px-2 py-1 rounded-sm bg-[#151b24] border border-[#1d2635] text-slate-300">
                  {lv.label}: ${fmt(lv.price, 1)}
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="mt-3 font-mono-t text-[10px] text-slate-500">
          timeframe {agent.timeframe} · valid: {String(agent.valid)}
        </div>
      </div>
    </div>
  );
};

/**
 * ObservationLayer — replaces AgentMatrix as Layer 3.
 *
 * Grid matches the approved mockup exactly:
 *   Row 1: Structure | Breakout | Location
 *   Row 2: Momentum & trend | Volume | Pattern
 *   Then: Context (Elliott Wave), dimmed, full width
 *
 * Same data source as before (analysis.agents + analysis.price,
 * AgentResult-shaped — no backend change, no new API calls).
 */
export const ObservationLayer = ({ agents = [], price }) => {
  const [selected, setSelected] = useState(null);
  const byId = Object.fromEntries(agents.map((a) => [a.agent, a]));

  const structure = byId["market_structure"];
  const breakout = byId["breakout"];
  const sr = byId["support_resistance"];
  const fib = byId["fibonacci"];
  const fvg = byId["fair_value_gap"];
  const momentum = byId["momentum"];
  const trend = byId["trend"];
  const volume = byId["volume"];

  return (
    <div data-testid="observation-layer">
      <div className="flex items-center gap-2 mb-3 px-0.5">
        <span className="font-head font-bold text-slate-200 tracking-wide text-lg">OBSERVATIONS</span>
        <span className="widget-label">Layer 3 · grouped by role, not vote tally</span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3">
        {structure && <ObservationCard agent={structure} onClick={setSelected} />}
        {breakout && <ObservationCard agent={breakout} onClick={setSelected} />}
        {(momentum || trend) && (
          <MomentumTrendCard momentum={momentum} trend={trend} onClick={setSelected} />
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {(sr || fib || fvg) && (
          <LocationCard sr={sr} fib={fib} fvg={fvg} price={price} onClick={setSelected} />
        )}
        {volume && <ObservationCard agent={volume} onClick={setSelected} />}
      </div>

      <AgentDetailModal agent={selected} onClose={() => setSelected(null)} />
    </div>
  );
};