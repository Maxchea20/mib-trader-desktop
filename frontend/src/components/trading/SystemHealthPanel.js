import React from "react";

const fmtAge = (ts) => {
  if (!ts) return "—";
  const secs = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
};

/**
 * Read-only. Sources entirely from data already fetched by App.js:
 *  - syncStatus (/market/sync-status, already polled every 15s) for
 *    per-timeframe M1/M5/M15 candle health.
 *  - auto.state (/autotrade) for connection + last evaluation time.
 * No new backend fields -- both endpoints already existed and were
 * already being polled; this data just wasn't rendered anywhere.
 */
export const SystemHealthPanel = ({ syncStatus, auto }) => {
  const tfs = syncStatus?.timeframes || {};
  const live = syncStatus?.live || {};
  const lastEval = auto?.state?.last_eval_at;
  const connected = !!(auto?.state?.connected ?? live.connected);

  const row = (label, tf) => {
    const info = tfs[tf];
    const ok = info && info.status !== "error" && info.status !== "failed";
    return (
      <div key={tf} className="border border-[#1d2635] bg-[#0d121b] p-2" data-testid={`health-${tf}`}>
        <div className="widget-label">{label}</div>
        <div className={`font-mono-t text-sm mt-1 ${ok ? "text-emerald-400" : "text-rose-400"}`}>
          {info ? (ok ? "OK" : (info.status || "ERROR")) : "NO DATA"}
        </div>
        <div className="font-mono-t text-[10px] text-slate-500 mt-0.5">
          last candle {fmtAge(info?.last_ts)} · {info?.count ?? 0} stored
        </div>
      </div>
    );
  };

  return (
    <div className="panel p-3" data-testid="system-health-panel">
      <div className="flex items-center justify-between mb-2">
        <span className="font-head font-bold text-slate-200 tracking-wide text-sm">SYSTEM / DATA HEALTH</span>
        <span className={`font-mono-t text-[10px] px-2 py-0.5 rounded-sm border ${connected ? "text-emerald-400 border-emerald-500/40" : "text-rose-400 border-rose-500/40"}`}>
          {connected ? "● CONNECTED" : "○ DISCONNECTED"}
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {row("M1 (Case-1 / C)", "1m")}
        {row("M5", "5m")}
        {row("M15", "15m")}
        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">Last evaluation</div>
          <div className="font-mono-t text-sm mt-1 text-slate-200">{fmtAge(lastEval)}</div>
          <div className="font-mono-t text-[10px] text-slate-500 mt-0.5">
            {live.source ? `source: ${live.source}` : "\u00A0"}
          </div>
        </div>
      </div>
    </div>
  );
};