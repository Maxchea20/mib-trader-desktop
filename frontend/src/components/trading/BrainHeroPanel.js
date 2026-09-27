import React from "react";

export const BrainHeroPanel = ({ s1, hunt, weather, auto }) => {
  const brain = s1 || hunt || {};
  const alive = Boolean(brain && (brain.ok || brain.brain_version || brain.action));
  const action = brain?.action || (alive ? "WAIT" : "—");
  const side = brain?.direction || "—";
  const path = brain?.timing || "—";
  const level = brain?.thesis_level;
  const size = brain?.size || "—";
  const flag = weather?.flag || "—";
  const slot = brain?.slot;
  const event = brain?.event;
  const timing = brain?.timing || "—";
  const phase = brain?.timing_state || "—";
  const why = (brain?.why_state || [])[0];
  const st = auto?.state || auto || {};
  const equity = st.equity ?? st.available_balance;
  const openN = st.open_positions ?? 0;

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
        <span className="widget-label">S1 Brain</span>
        <span className="widget-label">S1 / S2 + C</span>
      </div>

      <div className="flex items-center justify-between mt-2 font-mono-t text-[10px]">
        <span className={alive ? "text-emerald-400" : "text-slate-500"}>
          {alive ? "● S1 is on" : "○ Brain not answering"}
        </span>
        <span className="text-slate-400">{openN > 0 ? `Isolated OPEN ${openN}` : "Isolated FLAT"}</span>
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
            {action === "FIRE" ? `Take the ${side}` : side && side !== "—" && side !== "NEUTRAL" ? `${side} thesis · watching` : alive ? "Watching. No trade." : "Brain not answering"}
          </div>
        </div>
        <div className="text-right">
          <div className="widget-label">Timing</div>
          <div className="font-mono-t font-bold text-xl text-cyan-400">{timing} · {phase}</div>
          <div className="font-mono-t text-[10px] text-slate-500 mt-1">{path}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-[#1d2635] font-mono-t text-[11px]">
        <div>
          <div className="widget-label">4h weather</div>
          <div className="text-slate-200 mt-0.5">{flag}</div>
        </div>
        <div>
          <div className="widget-label">Thesis origin</div>
          <div className="text-slate-200 mt-0.5">{level != null ? Number(level).toFixed(1) : "—"}</div>
        </div>
        <div>
          <div className="widget-label">Equity</div>
          <div className="text-slate-200 mt-0.5">{equity != null ? `$${Number(equity).toFixed(2)}` : size}</div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 mt-2 font-mono-t text-[10px] text-slate-400">
        <div>5m candle {slot != null ? slot : "—"} of 3</div>
        <div>{side && side !== "NEUTRAL" && side !== "—" ? "15m setup: ready" : "15m setup: none"}</div>
        <div>{event || "no CHoCH / BOS"}</div>
      </div>
      {why ? (
        <div className="mt-2 font-mono-t text-[10px] text-slate-500 truncate" title={String(why)}>
          {String(why)}
        </div>
      ) : null}
    </div>
  );
};
