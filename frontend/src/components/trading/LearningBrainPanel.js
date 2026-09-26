import React, { useEffect, useState } from "react";
import { getLearningStatus } from "../../lib/api";
import "./LearningBrainPanel.css";

export const LearningBrainPanel = () => {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let active = true;
    let timeoutId = null;
    const tick = async () => {
      if (!active) return;
      try {
        const d = await getLearningStatus();
        if (active) {
          setData(d);
          setErr(null);
        }
      } catch (e) {
        if (active) setErr("offline");
      }
      if (active) timeoutId = setTimeout(tick, 8000);
    };
    tick();
    return () => {
      active = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, []);

  const offline = err === "offline" || data?.available === false || data?.stage === "OFFLINE";
  const stage = offline ? "OFFLINE" : (data?.stage || "COLLECTING");
  const counts = data?.counts || {};
  const last = data?.last_snapshot;
  const slice = data?.slice || {};
  const hist = data?.history || {};
  const signal = data?.signal;
  const model = data?.model;
  const similar = counts.similar ?? signal?.similar_n;

  return (
    <div className="panel p-4 lb-panel" data-testid="learning-brain-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-head font-extrabold tracking-[0.22em] text-slate-100 text-sm">LEARNING BRAIN</div>
          <div className="widget-label mt-0.5">What historically happens — not Isolated</div>
        </div>
        <div className="text-right">
          <div className="flex items-center justify-end gap-1.5">
            <span className={`lb-led ${offline ? "lb-led-off" : "lb-led-on"}`} />
            <span className="font-mono-t text-[10px] text-slate-300">{offline ? "OFFLINE" : "ONLINE"}</span>
          </div>
          <div className="widget-label mt-1">VETO OFF · IMPACT NONE</div>
        </div>
      </div>

      <div className="mt-3 flex items-end justify-between">
        <div>
          <div className="font-head font-black text-2xl text-cyan-300 tracking-wide">{stage}</div>
          <div className="font-mono-t text-[10px] text-slate-500 mt-1">
            {model?.model_id ? `MODEL ${model.model_id}` : "MODEL NONE / NOT ENOUGH DATA"}
          </div>
        </div>
        <div className="text-right font-mono-t text-[11px] text-slate-300">
          <div>{counts.snapshots || 0} snapshots</div>
          <div>{counts.labeled || 0} labeled</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 mt-3">
        <div className="lb-card">
          <div className="widget-label">P(WIN)</div>
          <div className="font-mono-t text-[13px] text-slate-100 mt-1">
            {signal?.p_win != null ? `${Math.round(signal.p_win * 100)}%` : "—"}
          </div>
        </div>
        <div className="lb-card">
          <div className="widget-label">EXPECTED R</div>
          <div className="font-mono-t text-[13px] text-slate-100 mt-1">
            {signal?.expected_r != null ? `${signal.expected_r}R` : "—"}
          </div>
        </div>
        <div className="lb-card">
          <div className="widget-label">SIMILAR CASES</div>
          <div className="font-mono-t text-[13px] text-slate-100 mt-1">
            {similar != null ? similar : "—"}
          </div>
        </div>
        <div className="lb-card">
          <div className="widget-label">QUALITY</div>
          <div className="font-mono-t text-[13px] text-slate-100 mt-1">
            {signal?.quality || (hist.n ? `mean ${hist.mean_r}R` : "collecting")}
          </div>
        </div>
      </div>

      <div className="mt-3 lb-card">
        <div className="widget-label">CURRENT SLICE</div>
        <div className="font-mono-t text-[11px] text-slate-200 mt-1 truncate">
          {slice.direction || last?.action || "waiting for Hunt ticks"}
          {slice.event ? ` · ${slice.event}` : ""}
          {slice.timing ? ` · ${slice.timing}` : last?.timing ? ` · ${last.timing}` : ""}
          {slice.weather ? ` · ${slice.weather}` : ""}
        </div>
      </div>

      <div className="mt-2 flex justify-between font-mono-t text-[10px] text-slate-400">
        <span>C ok {counts.c_success || 0}</span>
        <span>C miss {counts.c_miss || 0}</span>
      </div>

      <div className="mt-3 pt-3 border-t border-[#1d2635] font-mono-t text-[10px] text-slate-600">
        Does not fire. Isolated follows Hunt.
      </div>
    </div>
  );
};
