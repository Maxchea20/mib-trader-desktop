import React from "react";

function FactCard({ obs }) {
  if (!obs) return null;
  const ev = (obs.history || []).slice(-3);
  return (
    <div className="panel p-3">
      <div className="widget-label">{String(obs.source || "").replace(/_/g, " ")}</div>
      <div className="font-head font-bold text-white text-lg mt-0.5">{obs.state || "—"}</div>
      <div className="font-mono-t text-[10px] text-slate-500 mt-1">{obs.observation_type}</div>
      <div className="flex flex-wrap gap-1 mt-2">
        {(obs.tags || []).slice(0, 8).map((t) => (
          <span key={t} className="font-mono-t text-[9px] px-1.5 py-0.5 border border-[#1d2635] text-slate-400">{t}</span>
        ))}
      </div>
      {ev.map((e, i) => (
        <div key={i} className="font-mono-t text-[10px] text-cyan-400 mt-1">
          {e.event_type} {e.direction || ""}
        </div>
      ))}
    </div>
  );
}

export const ObservationLayer = ({ observations = [], hunt, weather }) => {
  const path = hunt && hunt.hunt && hunt.hunt.m5_path;
  const why = hunt && (hunt.why_state || [])[0];
  const action = hunt ? hunt.action : "—";

  return (
    <div data-testid="observation-layer">
      <div className="flex items-center gap-2 mb-3 px-0.5">
        <span className="font-head font-bold text-slate-200 tracking-wide text-lg">OBSERVATIONS</span>
        <span className="widget-label">Layer 3 · observe() facts + hunt path</span>
      </div>

      <div className="panel p-3 mb-3 flex flex-wrap gap-6 items-center">
        <div>
          <div className="widget-label">Hunt</div>
          <div className="font-head font-bold text-white text-xl">{action}</div>
        </div>
        <div>
          <div className="widget-label">5m path</div>
          <div className="font-mono-t text-cyan-400">{path || "—"}</div>
        </div>
        <div>
          <div className="widget-label">Weather</div>
          <div className="font-mono-t text-slate-200">{weather && weather.flag ? weather.flag : "—"}</div>
        </div>
        <div className="font-mono-t text-[11px] text-slate-400 max-w-xl">{why}</div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {(observations || []).map((o) => (
          <FactCard key={o.source} obs={o} />
        ))}
      </div>
    </div>
  );
};
