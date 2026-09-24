import React from "react";

function glowRgb(text) {
  const s = String(text || "").toUpperCase();
  if (s.includes("FIRE") && (s.includes("LONG") || s.includes("BULL"))) return "0,245,155";
  if (s.includes("FIRE") && (s.includes("SHORT") || s.includes("BEAR"))) return "255,59,86";
  if (s.includes("BULL") || s.includes("LONG")) return "0,245,155";
  if (s.includes("BEAR") || s.includes("SHORT")) return "255,59,86";
  if (s.includes("WAIT") || s.includes("CHOP") || s.includes("NEUTRAL")) return "251,191,36";
  return "148,163,184";
}

function FactCard({ obs }) {
  if (!obs) return null;
  const ev = (obs.history || []).slice(-3);
  const rgb = glowRgb(obs.state);
  return (
    <div
      className="breathe-glow panel p-3"
      style={{ ["--glow-rgb"]: rgb, borderColor: `rgba(${rgb},0.55)` }}
    >
      <div className="widget-label">{String(obs.source || "").replace(/_/g, " ")}</div>
      <div
        className="font-head font-bold text-lg mt-0.5 pulse-dot"
        style={{ color: `rgb(${rgb})`, textShadow: `0 0 12px rgba(${rgb},0.55)` }}
      >
        {obs.state || "—"}
      </div>
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

function scenarioLabelForSlot(slot) {
  if (slot == null) return "—";
  return `M5#${slot} (C)`;
}

export const ObservationLayer = ({ observations = [], weather, auto }) => {
  const result = auto?.state?.last_scenario_result;

  // Engine-agnostic facts (Market Structure / Breakout / S/R / FVG /
  // Volume / Trend / Momentum below) are unchanged either way -- they
  // come from the same observe() agents scenario_engine.py itself
  // calls internally, not from either engine's decision layer. Only
  // this top strip actually differs by which engine is active.
  // The scenario engine is the only entry engine; Hunt's own
  // FIRE/WAIT is not shown here because it never opens a trade.
  const action = result ? (result.action === "ALREADY_ATTEMPTED" ? "HANDLED" : result.action) : "WAIT";
  const path = scenarioLabelForSlot(result?.m5_slot);
  const why = result?.reason || "No active M15 thesis yet.";
  const side = result?.direction || "";
  const rgb = glowRgb(`${action} ${side}`);

  return (
    <div data-testid="observation-layer">
      <div className="flex items-center gap-2 mb-3 px-0.5">
        <span className="font-head font-bold text-slate-200 tracking-wide text-lg">OBSERVATIONS</span>
        <span className="widget-label">
          Layer 3 · observe() facts + scenario path
        </span>
      </div>

      <div
        className="breathe-glow panel p-3 mb-3 flex flex-wrap gap-6 items-center"
        style={{ ["--glow-rgb"]: rgb, borderColor: `rgba(${rgb},0.65)` }}
        data-testid="observation-top-strip"
      >
        <div>
          <div className="widget-label">Scenario</div>
          <div
            className="font-head font-bold text-xl pulse-dot"
            style={{ color: `rgb(${rgb})`, textShadow: `0 0 14px rgba(${rgb},0.7)` }}
          >
            {action}
          </div>
        </div>
        <div>
          <div className="widget-label">M5 slot</div>
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