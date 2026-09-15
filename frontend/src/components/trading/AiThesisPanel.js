import React, { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { getAiThesis } from "../../lib/api";

const fmtAgo = (ts) => {
  if (!ts) return "—";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
};

// The orb itself: a small inline SVG circle. Idle = slow single-color
// breathing glow. Processing = faster pulse + a continuous hue sweep, so it
// visibly reads as "thinking" the moment the backend starts a new OpenAI
// call. Both states are pure CSS (see index.css .ai-orb-idle / .ai-orb-processing)
// — no animation loop in JS.
const Orb = ({ processing }) => (
  <div
    className={`w-9 h-9 rounded-full flex-shrink-0 ${processing ? "ai-orb-processing" : "ai-orb-idle"}`}
    style={{
      background: processing
        ? "radial-gradient(circle at 35% 30%, #fff, #22d3ee 35%, #a855f7 70%, #0d121b 100%)"
        : "radial-gradient(circle at 35% 30%, #d7fbff, #22d3ee 45%, #0d121b 100%)",
    }}
    data-testid="ai-orb"
  />
);

// Purely read-only: polls the backend's last-computed thesis every 5s. This
// component never triggers a new OpenAI call itself — the 15-min cadence is
// entirely owned by the backend loop (ai_thesis.py). There is no write path
// here at all; it cannot place, modify, or affect any trade.
export const AiThesisPanel = () => {
  const [data, setData] = useState(null);

  useEffect(() => {
    let active = true;
    let timeoutId = null;
    const tick = async () => {
      if (!active) return;
      try {
        const d = await getAiThesis();
        if (active) setData(d);
      } catch (e) {}
      if (active) timeoutId = setTimeout(tick, 5000);
    };
    tick();
    return () => {
      active = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, []);

  const cfg = data?.config || {};
  const st = data?.state || {};
  const processing = Boolean(st.processing);
  const aligned = st.aligned_with_brain;

  return (
    <div className="panel p-4" data-testid="ai-thesis-panel">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-cyan-400" />
          <span className="widget-label">AI Thesis</span>
        </div>
        <span className="font-mono-t text-[10px] text-slate-500">
          {processing ? "thinking…" : `updated ${fmtAgo(st.generated_at)}`}
        </span>
      </div>

      <div className="flex items-start gap-3">
        <Orb processing={processing} />
        <div className="flex-1 min-w-0">
          {st.error && !st.thesis && (
            <div className="font-mono-t text-[11px] text-rose-300">
              Couldn't generate a read yet: {st.error}
            </div>
          )}
          {!st.error && !st.thesis && !processing && (
            <div className="font-mono-t text-[11px] text-slate-500">
              Waiting for the first analysis cycle (every {Math.round((cfg.interval_seconds || 900) / 60)} min).
            </div>
          )}
          {st.thesis && (
            <>
              {aligned !== null && aligned !== undefined && (
                <span
                  className={`inline-block mb-1.5 px-2 py-0.5 rounded-sm font-mono-t text-[10px] border ${
                    aligned
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                      : "border-amber-500/40 bg-amber-500/10 text-amber-300"
                  }`}
                  data-testid="ai-aligned-badge"
                >
                  {aligned ? "Aligned with Brain" : "Diverges from Brain"}
                </span>
              )}
              <div className="font-mono-t text-[12px] text-slate-200 leading-relaxed">
                {st.thesis}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="mt-3 pt-3 border-t border-[#1d2635] font-mono-t text-[10px] text-slate-500">
        Read-only market commentary — does not trade and cannot affect the backend or any live order.
      </div>
    </div>
  );
};