import React from "react";
import { ListTree } from "lucide-react";

const PLAIN = {
  "C: 15m is not a V2 arm":
    "This 15-minute candle has no CHoCH and no first break of structure. Nothing to hunt. Sitting out.",
  "C: 5m #3 did not close through prior 15m range":
    "The third 5-minute candle did not close through the last 15-minute high or low. No entry.",
  "C: no V2 tap on this 5m":
    "This 5-minute candle did not tap the 15-minute level. Waiting.",
  "C: no hunt side":
    "Setup has no long or short side. Sitting out.",
  "missing candles":
    "Need more candles before Hunt C can look.",
};

function plainLine(line) {
  if (!line) return line;
  if (PLAIN[line]) return PLAIN[line];
  if (line.startsWith("C: skip ")) {
    return line
      .replace("C: skip ", "This 15-minute move is only a ")
      .replace(" — only CHoCH / first BOS", ", not a CHoCH or first break of structure. Skip.");
  }
  if (line.startsWith("C: 4h weather ")) {
    return line.replace("C: 4h weather ", "4-hour weather is ").replace(" blocks ", ", so no ") + " trade now.";
  }
  return line;
}

export const ExplainabilityPanel = ({ hunt, weather }) => {
  const reasons = (hunt?.why_state || []).map(plainLine);
  const path = hunt?.hunt?.m5_path || hunt?.v3a_path;
  const event = hunt?.event;
  const flag = weather?.flag || hunt?.weather_flag;
  const slot = hunt?.slot ?? hunt?.hunt?.slot;
  const armed = hunt?.armed ?? hunt?.hunt?.armed;
  const alive = Boolean(hunt && (hunt.ok || hunt.brain_version || hunt.action));

  return (
    <div className="panel p-4" data-testid="explainability-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ListTree className="w-3.5 h-3.5 text-cyan-400" />
          <span className="widget-label">Why {hunt?.action === "FIRE" ? "FIRE" : "waiting"}?</span>
        </div>
        <span className={`font-mono-t text-[10px] ${alive ? "text-emerald-400" : "text-slate-500"}`}>
          {alive ? "Brain answered" : "Brain not answering"}
        </span>
      </div>
      <div className="space-y-1.5 mb-3">
        {!alive && (
          <div className="font-mono-t text-[11px] text-slate-500">Hunt C has not sent a result yet. Pull latest and recompute.</div>
        )}
        {alive && reasons.length === 0 && (
          <div className="font-mono-t text-[11px] text-slate-400">Hunt C ran. No extra note.</div>
        )}
        {reasons.map((r, i) => (
          <div key={i} className="font-mono-t text-[11px] text-slate-300 leading-relaxed">{r}</div>
        ))}
      </div>
      <div className="pt-3 border-t border-[#1d2635] space-y-1 font-mono-t text-[11px] text-slate-400">
        {slot != null && <div>Which 5m inside the 15m: candle {slot} of 3</div>}
        <div>15m setup: {armed ? "ready (CHoCH or first BOS)" : "none — not a CHoCH / first BOS"}</div>
        {event && <div>15m event: {event}</div>}
        {path && path !== "idle" && <div>5m status: {path}</div>}
        {flag && <div>4h weather: {flag}</div>}
      </div>
    </div>
  );
};
