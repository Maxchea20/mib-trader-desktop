import React from "react";

export const BrainHeroPanel = ({ hunt, weather }) => {
  const alive = Boolean(hunt && (hunt.ok || hunt.brain_version || hunt.action));
  const action = hunt?.action || (alive ? "WAIT" : "—");
  const side = hunt?.direction || "—";
  const rawPath = hunt?.hunt?.m5_path || hunt?.v3a_path;
  const path = !rawPath || rawPath === "idle" ? (alive ? "waiting" : "—") : rawPath;
  const level = hunt?.hunt?.level;
  const size = hunt?.size || "—";
  const flag = weather?.flag || hunt?.weather_flag || "—";
  const slot = hunt?.slot ?? hunt?.hunt?.slot;
  const armed = hunt?.armed ?? hunt?.hunt?.armed;
  const event = hunt?.event;

  let rgb = "148,163,184";
  if (!alive) rgb = "71,85,105";
  else if (action === "FIRE" && side === "LONG") rgb = "0,245,155";
  else if (action === "FIRE" && side === "SHORT") rgb = "255,59,86";
  else if (action === "WAIT") rgb = "251,191,36";

  const color = `rgb(${rgb})`;

  return (
    <div
      className="breathe-glow panel p-4 relative overflow-hidden"
      style={{ ["--glow-rgb"]: rgb, borderColor: `rgba(${rgb},0.65)` }}
      data-testid="brain-decision-hero"
    >
      <div className="flex items-center justify-between">
        <span className="widget-label">Hunt Brain</span>
        <span className="widget-label">15m setup · 5m entry</span>
      </div>

      <div className="flex items-center justify-between mt-2 font-mono-t text-[10px]">
        <span className={alive ? "text-emerald-400" : "text-slate-500"}>
          {alive ? "● Hunt C is on" : "○ Brain not answering"}
        </span>
        <span className="text-slate-400">Hunt C</span>
      </div>

      <div className="flex items-end justify-between mt-3">
        <div>
          <div
            className="font-head font-black text-5xl leading-none pulse-dot"
            style={{ color, textShadow: `0 0 18px rgba(${rgb},0.7)` }}
            data-testid="brain-decision-badge"
          >
            {action === "WAIT" ? "WAIT" : action === "FIRE" ? "FIRE" : action}
          </div>
          <div className="font-mono-t text-[11px] text-slate-400 mt-2">
            {action === "FIRE" ? `Take the ${side}` : alive ? "Watching. No trade." : "Brain not answering"}
          </div>
        </div>
        <div className="text-right">
          <div className="widget-label">What the 5m is doing</div>
          <div className="font-mono-t font-bold text-xl text-cyan-400">{path}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-[#1d2635] font-mono-t text-[11px]">
        <div>
          <div className="widget-label">4h weather</div>
          <div className="text-slate-200 mt-0.5">{flag}</div>
        </div>
        <div>
          <div className="widget-label">Entry level</div>
          <div className="text-slate-200 mt-0.5">{level != null ? Number(level).toFixed(1) : "—"}</div>
        </div>
        <div>
          <div className="widget-label">Size</div>
          <div className="text-slate-200 mt-0.5">{action === "FIRE" ? size : "—"}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mt-2 font-mono-t text-[10px] text-slate-400">
        <div>5m candle {slot != null ? slot : "—"} of 3</div>
        <div>{armed ? "15m setup: ready" : "15m setup: none"}</div>
        <div>{event || "no CHoCH / BOS"}</div>
      </div>
    </div>
  );
};
