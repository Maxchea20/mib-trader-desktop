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
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70" onClick={onClose}>
      <div className="panel max-w-lg w-full p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <div className="widget-label">Agent {meta.num}</div>
            <div className="font-head font-bold text-2xl text-white">{meta.name}</div>
          </div>
          <button onClick={onClose} className="text-slate-500"><X className="w-5 h-5" /></button>
        </div>
        <div className="grid grid-cols-3 gap-3 mt-4">
          <div className="panel p-2"><div className="widget-label">Direction</div><div className={`font-head font-bold ${st.text}`}>{agent.direction}</div></div>
          <div className="panel p-2"><div className="widget-label">Confidence</div><div className="font-mono-t font-bold">{fmt(agent.confidence, 0)}%</div></div>
          <div className="panel p-2"><div className="widget-label">Strength</div><div className="font-mono-t font-bold">{fmt(agent.strength, 0)}</div></div>
        </div>
      </div>
    </div>
  );
};

function FactCard({ obs }) {
  if (!obs) return null;
  const ev = (obs.history || []).slice(-2);
  return (
    <div className="panel p-3">
      <div className="widget-label">{obs.source}</div>
      <div className="font-head font-bold text-white text-lg mt-0.5">{obs.state || "—"}</div>
      <div className="font-mono-t text-[10px] text-slate-500 mt-1">{obs.observation_type}</div>
      <div className="flex flex-wrap gap-1 mt-2">
        {(obs.tags || []).slice(0, 6).map((t) => (
          <span key={t} className="font-mono-t text-[9px] px-1.5 py-0.5 border border-[#1d2635] text-slate-400">{t}</span>
        ))}
      </div>
      {ev.map((e, i) => (
        <div key={i} className="font-mono-t text-[10px] text-cyan-400 mt-1">{e.event_type} {e.direction}</div>
      ))}
    </div>
  );
}

export const ObservationLayer = ({ agents = [], observations = [], hunt, weather, price }) => {
  const [selected, setSelected] = useState(null);
  const byId = Object.fromEntries(agents.map((a) => [a.agent, a]));
  const bySrc = Object.fromEntries((observations || []).map((o) => [o.source, o]));
  const structure = byId["market_structure"];
  const breakout = byId["breakout"];
  const sr = byId["support_resistance"];
  const fib = byId["fibonacci"];
  const fvg = byId["fair_value_gap"];
  const momentum = byId["momentum"];
  const trend = byId["trend"];
  const volume = byId["volume"];
  const path = hunt && hunt.hunt && hunt.hunt.m5_path;
  const why = hunt && (hunt.why_state || [])[0];

  return (
    <div data-testid="observation-layer">
      <div className="flex items-center gap-2 mb-3 px-0.5">
        <span className="font-head font-bold text-slate-200 tracking-wide text-lg">OBSERVATIONS</span>
        <span className="widget-label">Layer 3 · observe() facts + hunt path</span>
      </div>

      <div className="panel p-3 mb-3 flex flex-wrap gap-3 items-center">
        <div>
          <div className="widget-label">Hunt</div>
          <div className="font-head font-bold text-white">{hunt ? hunt.action : "—"}</div>
        </div>
        <div>
          <div className="widget-label">5m path</div>
          <div className="font-mono-t text-cyan-400">{path || "—"}</div>
        </div>
        <div>
          <div className="widget-label">Weather</div>
          <div className="font-mono-t text-slate-200">{weather && weather.flag ? weather.flag : "—"}</div>
        </div>
        <div className="font-mono-t text-[10px] text-slate-500 max-w-md">{why}</div>
      </div>

      {observations && observations.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
          {observations.map((o) => <FactCard key={o.source} obs={o} />)}
        </div>
      )}

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
