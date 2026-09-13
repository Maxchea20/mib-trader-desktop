import React from "react";

/** Hunt / V1b / weather panel for Full Simulation. */
export function HuntSimPanel({ weatherOn, onToggleWeather }) {
  return (
    <>
      <div className="font-mono-t text-[10px] mb-4 flex items-center gap-2 flex-wrap">
        <span className="text-slate-500">Engine:</span>
        <span className="px-2 py-0.5 rounded-sm border text-cyan-400 border-cyan-500/40">Hunt 15m / 5m</span>
        <span className="px-2 py-0.5 rounded-sm border text-cyan-400 border-cyan-500/40">V1b after-FIRE</span>
        <span className={`px-2 py-0.5 rounded-sm border ${weatherOn ? "text-emerald-400 border-emerald-500/40" : "text-slate-500 border-slate-700/50"}`}>
          Weather {weatherOn ? "ON" : "OFF"}
        </span>
      </div>
      <div className="panel p-3 mb-4">
        <div className="widget-label mb-2">What this run uses (current git Brain)</div>
        <ul className="font-mono-t text-[10px] text-slate-400 space-y-1 list-disc pl-4">
          <li>Entry: observation hunt on closed 15m, fill on 5m — not agent votes.</li>
          <li>After FIRE: HOLD / TRAIL / EXIT. Cash T0. Trail after +1R. Exit on 15m CHoCH or close through entry.</li>
          <li>Weather: closed 4h CHOP / SWING_UP / SWING_DOWN. Swing-up longs only. Swing-down shorts only.</li>
          <li>SL / TP from the six inputs above. V1b can flatten before TP.</li>
        </ul>
        <div className="flex items-center justify-between mt-3">
          <div className="widget-label">4h weather gate</div>
          <button onClick={onToggleWeather} type="button"
            className={`font-mono-t text-[10px] px-3 py-1 rounded-full border ${weatherOn ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-400" : "bg-slate-700/30 border-slate-600/50 text-slate-400"}`}>
            {weatherOn ? "ON" : "OFF"}
          </button>
        </div>
      </div>
    </>
  );
}
