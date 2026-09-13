import React from "react";

export const BrainHeroPanel = ({ hunt, weather }) => {
  const action = hunt?.action || "WAIT";
  const side = hunt?.direction || "—";
  const path = hunt?.hunt?.m5_path || "—";
  const level = hunt?.hunt?.level;
  const size = hunt?.size || "—";
  const flag = weather?.flag || "—";
  const color =
    action === "FIRE" && side === "LONG" ? "#00f59b"
    : action === "FIRE" && side === "SHORT" ? "#ff3b56"
    : "#94a3b8";

  return (
    <div className="panel p-4" data-testid="brain-decision-hero">
      <div className="flex items-center justify-between">
        <span className="widget-label">Hunt Brain</span>
        <span className="widget-label">15m arm · 5m fill</span>
      </div>
      <div className="flex items-end justify-between mt-3">
        <div>
          <div className="font-head font-black text-5xl leading-none" style={{ color }} data-testid="brain-decision-badge">
            {action}
          </div>
          <div className="font-mono-t text-[11px] text-slate-400 mt-2">
            {action === "FIRE" ? side : "no click"}
          </div>
        </div>
        <div className="text-right">
          <div className="widget-label">5m path</div>
          <div className="font-mono-t font-bold text-2xl text-cyan-400">{path}</div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-[#1d2635] font-mono-t text-[11px]">
        <div>
          <div className="widget-label">Weather</div>
          <div className="text-slate-200 mt-0.5">{flag}</div>
        </div>
        <div>
          <div className="widget-label">Level</div>
          <div className="text-slate-200 mt-0.5">{level != null ? Number(level).toFixed(1) : "—"}</div>
        </div>
        <div>
          <div className="widget-label">Size</div>
          <div className="text-slate-200 mt-0.5">{action === "FIRE" ? size : "—"}</div>
        </div>
      </div>
    </div>
  );
};
