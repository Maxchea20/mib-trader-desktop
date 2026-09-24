import React, { useEffect, useMemo, useState } from "react";
import { getAiThesis } from "../../lib/api";
import "./AiThesisPanel.css";

const EVENT_LABEL = {
  REVIEW_15M: "M15 REVIEW",
  SETUP_ARMED: "SETUP ARMED",
  FIRE: "FIRE",
  EXIT: "EXIT",
  PULLBACK_TO_LEVEL: "PULLBACK TO LEVEL",
  THESIS_WEAK: "THESIS WEAK",
  THESIS_INVALID: "THESIS INVALID",
};

const STREAM_KINDS = new Set([
  "SETUP_ARMED",
  "FIRE",
  "EXIT",
  "PULLBACK_TO_LEVEL",
  "THESIS_WEAK",
  "THESIS_INVALID",
]);

const fmtClock = (ts) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

const fmtAgo = (ts) => {
  if (!ts) return "—";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
};

const scenarioIsFire = (sc, lastAction) => {
  const a = String(sc?.action || "").toUpperCase();
  const la = String(lastAction || "").toUpperCase();
  return a === "FIRE" || la.startsWith("SCENARIO OPEN") || la.startsWith("SCENARIO LIVE OPEN");
};

const visualFromState = (st, sc, lastAction) => {
  if (st.processing) return "analyzing";
  if (scenarioIsFire(sc, lastAction)) return "fire";
  const ev = String(st.last_event || "");
  if (ev === "EXIT" || ev === "THESIS_INVALID") return "invalid";
  if (ev === "THESIS_WEAK") return "weak";
  if (ev === "SETUP_ARMED" || ev === "PULLBACK_TO_LEVEL") return "setup";
  return "observing";
};

const VISUAL_COPY = {
  observing: { kicker: "ONLINE", line: "OBSERVING MARKET STRUCTURE" },
  analyzing: { kicker: "ONLINE", line: "ANALYZING MARKET" },
  setup: { kicker: "EVENT DETECTED", line: "SETUP DETECTED" },
  fire: { kicker: "EVENT DETECTED", line: "FIRE DETECTED" },
  weak: { kicker: "CAUTION", line: "THESIS WEAKENING" },
  invalid: { kicker: "ALERT", line: "THESIS INVALIDATED" },
};

const Card = ({ label, value }) => {
  if (value == null || value === "" || value === "none") return null;
  return (
    <div className="jarvis-card">
      <div className="widget-label">{label}</div>
      <div className="font-mono-t text-[11px] text-slate-200 truncate mt-1">{String(value)}</div>
    </div>
  );
};

const Core = ({ mode }) => (
  <div className={`jarvis-core jarvis-core-${mode}`} data-testid="ai-orb">
    <div className="jarvis-core-ring jarvis-core-ring-3" />
    <div className="jarvis-core-ring" />
    <div className="jarvis-core-ring jarvis-core-ring-2" />
    <div className="jarvis-core-ping" />
    <div className="jarvis-particle jarvis-particle-1" />
    <div className="jarvis-particle jarvis-particle-2" />
    <div className="jarvis-particle jarvis-particle-3" />
    <div className="jarvis-core-dot" />
    <div className="jarvis-core-copy">
      <div className="font-head tracking-[0.28em] text-[11px] text-slate-400">MIB INTELLIGENCE</div>
      <div className="font-head font-bold tracking-[0.18em] text-sm text-slate-100 mt-1">
        {VISUAL_COPY[mode]?.line || "OBSERVING"}
      </div>
    </div>
  </div>
);

export const AiThesisPanel = ({ analysis, auto }) => {
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
  const sc = auto?.state?.last_scenario_result || auto?.last_scenario_result;
  const lastAction = auto?.state?.last_action || auto?.last_action;
  const mode = visualFromState(st, sc, lastAction);

  const hunt = analysis?.hunt || {};
  const weather = analysis?.weather || {};
  const structure = analysis?.market_state?.structure || {};
  const momentum = (analysis?.agents || []).find((a) => a.agent === "momentum");
  const interval = cfg.interval_seconds || 900;
  const nextReview = st.generated_at ? st.generated_at + interval : null;

  const cards = useMemo(() => {
    const rows = [];
    if (lastAction) rows.push(["ENGINE", lastAction]);
    if (structure.regime) rows.push(["M15 REGIME", structure.regime]);
    if (momentum?.direction) {
      rows.push(["MOMENTUM", `${momentum.direction}${momentum.confidence != null ? ` ${momentum.confidence}%` : ""}`]);
    }
    if (weather.flag) rows.push(["4H WEATHER", weather.flag]);
    if (hunt?.timing) rows.push(["TIMING", hunt.timing]);
    return rows;
  }, [structure.regime, momentum, weather.flag, lastAction, hunt?.timing]);

  return (
    <div className={`panel p-4 jarvis-shell jarvis-shell-${mode}`} data-testid="ai-thesis-panel">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="font-head font-extrabold tracking-[0.22em] text-slate-100 text-sm">MIB AI</div>
          <div className="widget-label mt-0.5">Observer only — not Isolated</div>
        </div>
        <div className="text-right">
          <div className="flex items-center justify-end gap-1.5">
            <span className={`jarvis-led jarvis-led-${processing ? "analyzing" : "online"}`} />
            <span className="font-mono-t text-[10px] text-slate-300">
              {processing ? "ANALYZING" : "ONLINE"}
            </span>
          </div>
          <div className="widget-label mt-1">{VISUAL_COPY[mode]?.kicker}</div>
        </div>
      </div>

      <Core mode={mode} />

      {cards.length > 0 && (
        <div className="grid grid-cols-2 gap-1.5 mt-3">
          {cards.map(([label, value]) => (
            <Card key={label} label={label} value={value} />
          ))}
        </div>
      )}

      <div className="jarvis-read mt-3" data-testid="ai-observation">
        <div className="widget-label mb-2">AI observation</div>
        {st.error && !st.thesis && (
          <div className="font-mono-t text-[11px] text-rose-300">Couldn't generate a read yet: {st.error}</div>
        )}
        {!st.error && !st.thesis && !processing && (
          <div className="font-mono-t text-[11px] text-slate-500">
            Waiting for the first analysis cycle (every {Math.round(interval / 60)} min).
          </div>
        )}
        {st.thesis && (
          <>
            {aligned !== null && aligned !== undefined && (
              <span
                className={`inline-block mb-2 px-2 py-0.5 rounded-sm font-mono-t text-[10px] border ${
                  aligned
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-amber-500/40 bg-amber-500/10 text-amber-300"
                }`}
                data-testid="ai-aligned-badge"
              >
                {aligned ? "Aligned with Hunt" : "Diverges from Hunt"}
              </span>
            )}
            {st.last_event && STREAM_KINDS.has(st.last_event) && (
              <div className="font-mono-t text-[10px] text-cyan-300/80 mb-1.5">
                Wake · {EVENT_LABEL[st.last_event] || st.last_event}
              </div>
            )}
            <div className="font-mono-t text-[13px] text-slate-100 leading-relaxed">{st.thesis}</div>
          </>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono-t text-[10px] text-slate-500">
        <span>LAST {fmtClock(st.generated_at)} ({fmtAgo(st.generated_at)})</span>
        {nextReview && <span>NEXT NOTE ~ {fmtClock(nextReview)}</span>}
      </div>

      <div className="mt-3 pt-3 border-t border-[#1d2635] font-mono-t text-[10px] text-slate-600">
        Observer only. Isolated follows Hunt FIRE, not this panel.
      </div>
    </div>
  );
};
