import React from "react";
import { ListTree } from "lucide-react";

export const ExplainabilityPanel = ({ hunt, weather }) => {
  const reasons = hunt?.why_state || [];
  const path = hunt?.hunt?.m5_path;
  const event = hunt?.event;
  const primary = hunt?.primary;
  const vol = hunt?.volume_state;
  const htf = hunt?.htf_trend;
  const flag = weather?.flag;

  return (
    <div className="panel p-4" data-testid="explainability-panel">
      <div className="flex items-center gap-2 mb-3">
        <ListTree className="w-3.5 h-3.5 text-cyan-400" />
        <span className="widget-label">Why {hunt?.action || "WAIT"}? — Hunt IFs</span>
      </div>
      <div className="space-y-1.5 mb-3">
        {reasons.length === 0 && (
          <div className="font-mono-t text-[11px] text-slate-500">No hunt snapshot yet.</div>
        )}
        {reasons.map((r, i) => (
          <div key={i} className="font-mono-t text-[11px] text-slate-300">→ {r}</div>
        ))}
      </div>
      <div className="pt-3 border-t border-[#1d2635] space-y-1 font-mono-t text-[11px] text-slate-400">
        {primary && <div>15m trigger: {primary} {event}</div>}
        {path && <div>5m path: {path}</div>}
        {htf && <div>4h trend: {htf}</div>}
        {vol && <div>volume: {vol}</div>}
        {flag && <div>weather: {flag}</div>}
      </div>
    </div>
  );
};
