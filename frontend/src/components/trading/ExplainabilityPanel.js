import React from "react";
import { ListTree } from "lucide-react";

export const ExplainabilityPanel = ({ hunt, weather }) => {
  const reasons = hunt?.why_state || [];
  const path = hunt?.hunt?.m5_path || hunt?.v3a_path;
  const event = hunt?.event;
  const primary = hunt?.primary;
  const vol = hunt?.volume_state;
  const htf = hunt?.htf_trend;
  const flag = weather?.flag || hunt?.weather_flag;
  const version = hunt?.brain_version;
  const slot = hunt?.slot ?? hunt?.hunt?.slot;
  const armed = hunt?.armed ?? hunt?.hunt?.armed;
  const alive = Boolean(hunt && (hunt.ok || hunt.brain_version || hunt.action));

  return (
    <div className="panel p-4" data-testid="explainability-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ListTree className="w-3.5 h-3.5 text-cyan-400" />
          <span className="widget-label">Why {hunt?.action || "WAIT"}? — Hunt IFs</span>
        </div>
        <span className={`font-mono-t text-[10px] ${alive ? "text-emerald-400" : "text-slate-500"}`}>
          {alive ? "snapshot ok" : "no snapshot"}
        </span>
      </div>
      <div className="space-y-1.5 mb-3">
        {!alive && (
          <div className="font-mono-t text-[11px] text-slate-500">No hunt snapshot yet. Recompute on 15m or pull latest main.</div>
        )}
        {alive && reasons.length === 0 && (
          <div className="font-mono-t text-[11px] text-slate-400">Hunt C ran. No why-line.</div>
        )}
        {reasons.map((r, i) => (
          <div key={i} className="font-mono-t text-[11px] text-slate-300">→ {r}</div>
        ))}
      </div>
      <div className="pt-3 border-t border-[#1d2635] space-y-1 font-mono-t text-[11px] text-slate-400">
        {version && <div>version: {version}</div>}
        {slot != null && <div>5m slot: {slot} / 3</div>}
        <div>15m arm: {armed ? "yes" : "no"}</div>
        {primary && <div>15m trigger: {primary} {event}</div>}
        {event && !primary && <div>event: {event}</div>}
        {path && <div>5m path: {path}</div>}
        {htf && <div>4h trend: {htf}</div>}
        {vol && <div>volume: {vol}</div>}
        {flag && <div>weather: {flag}</div>}
      </div>
    </div>
  );
};
