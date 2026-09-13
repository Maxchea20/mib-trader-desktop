import React from "react";
import { fmt } from "../../lib/style";

/**
 * LocationCard — merges support_resistance + fibonacci + fair_value_gap
 * into one "reference only" card, matching the approved mockup.
 *
 * Uses the exact same key_levels filtering KeyLevelsPanel.js (Layer 1)
 * already uses -- same real fields (type/price/low/high), no new
 * backend data. This intentionally shows similar information to
 * KeyLevelsPanel; that overlap was flagged as a design trade-off in
 * this session's summary rather than assumed away a second time.
 */
export const LocationCard = ({ sr, fib, fvg, price, onClick }) => {
  const supports = (sr?.key_levels || []).filter((l) => l.type === "support");
  const resistances = (sr?.key_levels || []).filter((l) => l.type === "resistance");
  const fibLevels = (fib?.key_levels || []).slice(0, 3);
  const fvgZones = (fvg?.key_levels || []).slice(0, 2);

  return (
    <div className="panel p-4 flex flex-col gap-2" data-testid="observation-card-location">
      <div className="flex items-center justify-between">
        <span className="font-head font-bold text-[15px] text-slate-50">Location</span>
        <span className="widget-label">reference only</span>
      </div>

      <div className="flex flex-col gap-1 mt-1">
        {resistances.slice(0, 1).map((l, i) => (
          <div key={`r${i}`} className="flex justify-between font-mono-t text-[11px]">
            <span className="text-rose-400" style={{ textShadow: "0 0 6px currentColor" }}>{l.label || "Resistance"}</span>
            <span className="text-slate-100 font-semibold">${fmt(l.price, 0)}</span>
          </div>
        ))}
        {price != null && (
          <div className="flex justify-between font-mono-t text-[11px] border-y border-[#1d2635] py-1 my-0.5">
            <span className="text-cyan-400 font-bold" style={{ textShadow: "0 0 6px currentColor" }}>PRICE</span>
            <span className="text-cyan-400 font-bold" style={{ textShadow: "0 0 6px currentColor" }}>${fmt(price, 0)}</span>
          </div>
        )}
        {supports.slice(0, 1).map((l, i) => (
          <div key={`s${i}`} className="flex justify-between font-mono-t text-[11px]">
            <span className="text-emerald-400" style={{ textShadow: "0 0 6px currentColor" }}>{l.label || "Support"}</span>
            <span className="text-slate-100 font-semibold">${fmt(l.price, 0)}</span>
          </div>
        ))}
        {fibLevels.map((l, i) => (
          <div key={`f${i}`} className="flex justify-between font-mono-t text-[11px]">
            <span className="text-purple-400" style={{ textShadow: "0 0 6px currentColor" }}>{l.label}</span>
            <span className="text-slate-100 font-semibold">${fmt(l.price, 0)}</span>
          </div>
        ))}
        {fvgZones.map((l, i) => (
          <div key={`g${i}`} className="flex justify-between font-mono-t text-[11px]">
            <span
              className={l.type === "fvg_bullish" ? "text-emerald-400" : "text-rose-400"}
              style={{ textShadow: "0 0 6px currentColor" }}
            >
              {l.type === "fvg_bullish" ? "Bull FVG" : "Bear FVG"}
            </span>
            <span className="text-slate-100 font-semibold">${fmt(l.low, 0)}–{fmt(l.high, 0)}</span>
          </div>
        ))}
        {supports.length === 0 && resistances.length === 0 && fibLevels.length === 0 && fvgZones.length === 0 && (
          <div className="font-mono-t text-[10px] text-slate-600">no levels near price</div>
        )}
      </div>

      <button
        type="button"
        onClick={() => onClick?.(sr || fib || fvg)}
        className="font-mono-t text-[9px] text-slate-600 mt-auto pt-1 text-left hover:text-slate-400"
      >
        support_resistance + fibonacci + fair_value_gap
      </button>
    </div>
  );
};