import React, { useState, useEffect, useRef } from "react";
import { X, Play, Square } from "lucide-react";
import {
  runWalkForwardBacktest, getWalkForwardStatus, getWalkForwardResult, stopWalkForwardBacktest,
} from "../../lib/api";
import { HuntSimPanel } from "./HuntSimPanel";

export const BacktestHuntModal = ({ open, onClose, timeframe }) => {
  const [days, setDays] = useState(7);
  const [balance, setBalance] = useState(250);
  const [capitalPct, setCapitalPct] = useState(10);
  const [leverage, setLeverage] = useState(5);
  const [sl, setSl] = useState(1.5);
  const [tp, setTp] = useState(2.5);
  const [weather, setWeather] = useState(true);
  const [runId, setRunId] = useState(null);
  const [status, setStatus] = useState(null);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);
  const alive = useRef(false);

  useEffect(() => () => { alive.current = false; if (pollRef.current) clearTimeout(pollRef.current); }, []);

  if (!open) return null;

  const stopPoll = () => {
    alive.current = false;
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
  };

  const run = async () => {
    setError(null); setResult(null); setProgress(0);
    try {
      const r = await runWalkForwardBacktest({
        timeframe,
        days: Number(days),
        start_balance: Number(balance),
        capital_pct: Number(capitalPct),
        leverage: Number(leverage),
        sl_atr_mult: Number(sl),
        tp_atr_mult: Number(tp),
        use_weather: weather,
        use_hunt_lifecycle: true,
        use_htf_gate: true,
        htf_timeframes: ["1h", "4h", "1d"],
      });
      if (r.error) { setError(r.error); return; }
      setRunId(r.id);
      setStatus("running");
      alive.current = true;
      const poll = async () => {
        if (!alive.current) return;
        try {
          const s = await getWalkForwardStatus(r.id);
          setProgress(s.progress || 0);
          if (s.status === "done" || s.status === "error" || s.status === "stopped") {
            stopPoll();
            setStatus(s.status);
            if (s.status === "error") { setError(s.error || "error"); return; }
            const res = await getWalkForwardResult(r.id);
            if (res.error) setError(res.error);
            else setResult(res);
            return;
          }
        } catch (e) {
          setError("lost connection");
          stopPoll();
          return;
        }
        pollRef.current = setTimeout(poll, 500);
      };
      poll();
    } catch (e) { setError("request failed"); }
  };

  const stop = async () => {
    if (runId) try { await stopWalkForwardBacktest(runId); } catch (e) {}
  };

  const stats = result && result.stats;
  const net = stats ? stats.net_pnl : null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="panel max-w-3xl w-full p-5 max-h-[92vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="font-head font-bold text-2xl text-white tracking-wide">BACKTEST \u00b7 HUNT + V1B</div>
            <div className="font-mono-t text-[10px] text-slate-500">15m hunt \u00b7 5m fill \u00b7 weather \u00b7 after-FIRE \u00b7 {timeframe}</div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>
        <div className="flex flex-wrap items-end gap-3 mb-3">
          {[
            ["Days to test", days, setDays, { min: 1, max: 400, step: 1, w: "w-20" }],
            ["Start balance ($)", balance, setBalance, { min: 1, step: 1, w: "w-24" }],
            ["Capital / trade (%)", capitalPct, setCapitalPct, { min: 1, max: 100, step: 1, w: "w-20" }],
            ["Leverage (x)", leverage, setLeverage, { min: 1, max: 20, step: 1, w: "w-16" }],
            ["SL (\u00d7 ATR)", sl, setSl, { min: 0.1, step: 0.1, w: "w-16" }],
            ["TP (\u00d7 ATR)", tp, setTp, { min: 0.1, step: 0.1, w: "w-16" }],
          ].map(([label, val, set, opt]) => (
            <label key={label} className="flex flex-col gap-1">
              <span className="widget-label">{label}</span>
              <input type="number" min={opt.min} max={opt.max} step={opt.step} value={val}
                onChange={(e) => set(e.target.value)}
                className={opt.w + " bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white"} />
            </label>
          ))}
          {status === "running" ? (
            <button onClick={stop} className="flex items-center gap-1.5 font-mono-t text-sm font-semibold px-4 py-1.5 rounded-sm bg-rose-600 text-white">
              <Square className="w-3.5 h-3.5" /> Stop
            </button>
          ) : (
            <button onClick={run} className="flex items-center gap-1.5 font-mono-t text-sm font-semibold px-4 py-1.5 rounded-sm bg-cyan-500 text-black">
              <Play className="w-3.5 h-3.5" /> Run Simulation
            </button>
          )}
        </div>
        <HuntSimPanel weatherOn={weather} onToggleWeather={() => setWeather((v) => !v)} />
        {status === "running" && (
          <div className="mb-4">
            <div className="h-1.5 bg-[#0d121b] rounded-full overflow-hidden border border-[#1d2635]">
              <div className="h-full bg-cyan-500" style={{ width: Math.round(progress * 100) + "%" }} />
            </div>
            <div className="font-mono-t text-[10px] text-slate-500 mt-1">{Math.round(progress * 100)}%</div>
          </div>
        )}
        {error && <div className="font-mono-t text-xs text-rose-400 mb-3">Error: {error}</div>}
        {stats && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div className="panel p-2.5">
              <div className="widget-label">Net $</div>
              <div className="font-mono-t font-bold text-lg" style={{ color: net >= 0 ? "#00f59b" : "#ff3b56" }}>{Number(net).toFixed(2)}</div>
            </div>
            <div className="panel p-2.5">
              <div className="widget-label">Trades</div>
              <div className="font-mono-t font-bold text-lg text-white">{stats.total_trades != null ? stats.total_trades : "—"}</div>
            </div>
            <div className="panel p-2.5">
              <div className="widget-label">Win %</div>
              <div className="font-mono-t font-bold text-lg text-white">{stats.win_rate != null ? Number(stats.win_rate).toFixed(1) : "—"}</div>
            </div>
            <div className="panel p-2.5">
              <div className="widget-label">PF</div>
              <div className="font-mono-t font-bold text-lg text-white">{stats.profit_factor != null ? Number(stats.profit_factor).toFixed(2) : "—"}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
