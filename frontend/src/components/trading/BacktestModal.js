import React, { useState, useEffect, useCallback, useRef } from "react";
import { X, Play, Square, AlertTriangle, Trash2 } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import {
  runBacktest, getBacktestLog, deleteBacktestLogEntry, clearBacktestLog,
  runWalkForwardBacktest, getWalkForwardStatus, getWalkForwardResult, stopWalkForwardBacktest,
  getWalkForwardLog, deleteWalkForwardLogEntry, clearWalkForwardLog,
  getSettings, updateSettings,
} from "../../lib/api";
import { dirStyle, fmt } from "../../lib/style";

// Matches settings.py's WEIGHT_MIN/WEIGHT_MAX on the backend — kept in
// sync deliberately, same reasoning as the backtest LIMITS above: the
// backend enforces this regardless, but clamping here too means you see
// the real value immediately instead of finding out only after saving.
const WEIGHT_LIMITS = { min: 0, max: 1 };

const Stat = ({ label, value, color, testid }) => (
  <div className="panel p-2.5">
    <div className="widget-label">{label}</div>
    <div className="font-mono-t font-bold text-lg mt-0.5" style={{ color: color || "#e2e8f0" }} data-testid={testid}>{value}</div>
  </div>
);

// Keep these in sync with the clamps in backend/src/backtest.py's
// run_backtest() — the backend clamps silently, so this exists purely to
// stop the UI from ever showing a value that doesn't match what actually
// ran. If you change one side, change the other.
const LIMITS = {
  forward: { min: 1, max: 48 },
  lookback: { min: 20, max: 2000 },
  step: { min: 1, max: 20 },
};

const clamp = (value, { min, max }) => {
  if (Number.isNaN(value)) return min;
  return Math.min(max, Math.max(min, value));
};

// Parses "15m", "1h", "4h", "1d" etc. into minutes, so the plain-English
// explanation below the inputs is correct no matter which timeframe this
// modal is opened for.
const tfToMinutes = (tf) => {
  const match = /^(\d+)([mhd])$/.exec(tf || "");
  if (!match) return 15; // sane fallback if the format is ever unexpected
  const n = parseInt(match[1], 10);
  const unit = match[2];
  if (unit === "m") return n;
  if (unit === "h") return n * 60;
  if (unit === "d") return n * 60 * 24;
  return 15;
};

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

// Human-readable labels for the non-weight settings sections, so the
// editor doesn't just show raw snake_case keys. Order here also controls
// display order.
const SECTION_FIELDS = {
  entry: [
    ["direction_min_score", "Direction min score"],
    ["entry_min_score", "Entry min score"],
    ["entry_min_confidence", "Entry min confidence %"],
    ["max_extension_pct", "Max extension %"],
    ["min_valid_agents", "Min valid agents"],
  ],
  conflict: [
    ["strong_confidence", "Strong confidence %"],
    ["contradiction_min_agents", "Min opposing agents"],
    ["severe_ratio", "Severe ratio"],
    ["veto_ratio", "Veto ratio"],
    ["confidence_penalty", "Confidence penalty"],
  ],
  htf_gate: [
    ["regime_min_score", "Regime min score"],
    ["counter_trend_penalty", "Counter-trend penalty"],
    ["counter_trend_confidence_penalty", "Counter-trend conf penalty"],
    ["aligned_bonus", "Aligned bonus"],
  ],
};

const RECOMMENDATION_STYLE = {
  upweight: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10",
  keep: "text-slate-300 border-slate-600/40 bg-slate-500/10",
  downweight: "text-amber-400 border-amber-500/40 bg-amber-500/10",
  drop: "text-rose-400 border-rose-500/40 bg-rose-500/10",
  "insufficient data": "text-slate-500 border-slate-700/40 bg-transparent",
};

export const BacktestModal = ({ open, onClose, timeframe }) => {
  const [tab, setTab] = useState("test");

  // --- Quick Test tab state (unchanged) ---
  const [forward, setForward] = useState(8);
  const [lookback, setLookback] = useState(150);
  const [step, setStep] = useState(3);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const [log, setLog] = useState([]);
  const [logLoading, setLogLoading] = useState(false);

  const refreshLog = useCallback(async () => {
    setLogLoading(true);
    try {
      const d = await getBacktestLog();
      setLog(d.runs || []);
    } catch (e) {}
    setLogLoading(false);
  }, []);

  useEffect(() => { if (open && tab === "log") refreshLog(); }, [open, tab, refreshLog]);

  // --- Walk-Forward Simulation tab state ---
  const [wfDays, setWfDays] = useState(7);
  const [wfBalance, setWfBalance] = useState(250);
  const [wfCapitalPct, setWfCapitalPct] = useState(10);
  const [wfLeverage, setWfLeverage] = useState(5);
  const [wfSlMult, setWfSlMult] = useState(1.5);
  const [wfTpMult, setWfTpMult] = useState(2.5);
  const [wfUseHtfGate, setWfUseHtfGate] = useState(true);
  const [wfHtfTfs, setWfHtfTfs] = useState(["4h", "1d"]); // matches live default
  const [wfRunId, setWfRunId] = useState(null);
  const [wfStatus, setWfStatus] = useState(null); // "running" | "done" | "error" | null
  const [wfProgress, setWfProgress] = useState(0);
  const [wfResult, setWfResult] = useState(null);
  const [wfError, setWfError] = useState(null);
  const wfPollRef = useRef(null);
  const wfPollingActiveRef = useRef(false);
  const wfPollRetriesRef = useRef(0);

  // Generalized across all four Settings panels (Agent Weights, Entry
  // Thresholds, Conflict/Contradiction, HTF Gate values) so every one of
  // them is testable here exactly like weights already were.
  //
  // Rewritten to use an explicit boolean "dirty" flag per section,
  // completely separate from the values object. Earlier this used
  // `testOverrides[section] !== null` as the signal, which mixed "has
  // this been touched" with "what shape is the values object" — this
  // version can never be ambiguous: dirty[section] is the ONLY thing
  // that decides whether a section counts as customized.
  const [liveSettings, setLiveSettings] = useState(null); // {agent_weights, entry, conflict, htf_gate}
  const [dirty, setDirty] = useState({ agent_weights: false, entry: false, conflict: false, htf_gate: false });
  const [overrideValues, setOverrideValues] = useState({ agent_weights: {}, entry: {}, conflict: {}, htf_gate: {} });
  const [savingWeights, setSavingWeights] = useState(false);
  const [saveMsg, setSaveMsg] = useState(null);

  const [wfLog, setWfLog] = useState([]);
  const [wfLogLoading, setWfLogLoading] = useState(false);
  const [expandedLogId, setExpandedLogId] = useState(null);
  const refreshWfLog = useCallback(async () => {
    setWfLogLoading(true);
    try {
      const d = await getWalkForwardLog();
      setWfLog(d.runs || []);
    } catch (e) {}
    setWfLogLoading(false);
  }, []);
  useEffect(() => { if (open && tab === "simlog") refreshWfLog(); }, [open, tab, refreshWfLog]);
  const removeWfLogEntry = async (id) => { await deleteWalkForwardLogEntry(id); refreshWfLog(); };
  const clearWfLogAll = async () => {
    if (!window.confirm("Clear the entire Full Simulation log? This can't be undone.")) return;
    await clearWalkForwardLog();
    refreshWfLog();
  };

  useEffect(() => {
    if (open && tab === "walkforward" && liveSettings === null) {
      getSettings().then((s) => setLiveSettings({
        agent_weights: s.agent_weights, entry: s.entry,
        conflict: s.conflict, htf_gate: s.htf_gate,
      })).catch(() => {});
    }
  }, [open, tab, liveSettings]);

  const isCustom = (section) => dirty[section];
  const currentValues = (section) => {
    if (dirty[section]) return overrideValues[section];
    return (liveSettings && liveSettings[section]) || {};
  };
  const setField = (section, key, value) => {
    const base = dirty[section] ? overrideValues[section] : ((liveSettings && liveSettings[section]) || {});
    setOverrideValues((prev) => ({ ...prev, [section]: { ...base, [key]: Number(value) } }));
    setDirty((prev) => ({ ...prev, [section]: true }));
  };
  const setWeightField = (engineId, value) => setField("agent_weights", engineId, value);
  const clampWeightField = (engineId, value) => {
    const n = Number(value);
    const clamped = Number.isNaN(n) ? WEIGHT_LIMITS.min : Math.min(WEIGHT_LIMITS.max, Math.max(WEIGHT_LIMITS.min, n));
    const base = dirty.agent_weights ? overrideValues.agent_weights : ((liveSettings && liveSettings.agent_weights) || {});
    setOverrideValues((prev) => ({ ...prev, agent_weights: { ...base, [engineId]: clamped } }));
    setDirty((prev) => ({ ...prev, agent_weights: true }));
  };
  const resetSection = (section) => {
    setDirty((prev) => ({ ...prev, [section]: false }));
    setOverrideValues((prev) => ({ ...prev, [section]: {} }));
  };
  const resetAllSections = () => {
    setDirty({ agent_weights: false, entry: false, conflict: false, htf_gate: false });
    setOverrideValues({ agent_weights: {}, entry: {}, conflict: {}, htf_gate: {} });
  };

  // Backwards-compatible aliases so the rest of the component (built
  // before this generalization) keeps working unchanged for weights.
  const currentWeights = currentValues("agent_weights");
  const isCustomWeights = isCustom("agent_weights");
  const resetToLiveWeights = () => resetSection("agent_weights");

  const saveAsLive = async () => {
    if (!wfResult) return;
    const payload = {};
    if (wfResult.config.used_custom_weights) payload.agent_weights = wfResult.weights_used;
    if (wfResult.config.used_custom_entry) payload.entry = wfResult.entry_used;
    if (wfResult.config.used_custom_conflict) payload.conflict = wfResult.conflict_used;
    if (wfResult.config.used_custom_htf_gate_values) payload.htf_gate = wfResult.htf_gate_used;
    if (Object.keys(payload).length === 0) return;
    if (!window.confirm(
      `This permanently changes your LIVE and PAPER trading ${Object.keys(payload).join(", ")} to the values used in this backtest run. Are you sure?`
    )) return;
    setSavingWeights(true); setSaveMsg(null);
    try {
      await updateSettings(payload);
      setLiveSettings((prev) => ({ ...prev, ...payload }));
      resetAllSections();
      setSaveMsg("Saved — these values are now live.");
    } catch (e) { setSaveMsg("Save failed."); }
    setSavingWeights(false);
  };

  const stopPolling = () => {
    wfPollingActiveRef.current = false;
    if (wfPollRef.current) { clearTimeout(wfPollRef.current); wfPollRef.current = null; }
  };
  useEffect(() => () => stopPolling(), []); // cleanup on unmount

  const HTF_CHOICES = ["30m", "1h", "4h", "1d"];
  const toggleHtfTf = (tf) => {
    setWfHtfTfs((prev) => prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf]);
  };

  const runWalkForward = async () => {
    setWfError(null); setWfResult(null); setWfProgress(0); setSaveMsg(null);
    try {
      const r = await runWalkForwardBacktest({
        timeframe, days: Number(wfDays), start_balance: Number(wfBalance),
        capital_pct: Number(wfCapitalPct), leverage: Number(wfLeverage),
        sl_atr_mult: Number(wfSlMult), tp_atr_mult: Number(wfTpMult),
        weights: isCustomWeights ? currentWeights : undefined,
        entry: isCustom("entry") ? currentValues("entry") : undefined,
        conflict: isCustom("conflict") ? currentValues("conflict") : undefined,
        htf_gate_values: isCustom("htf_gate") ? currentValues("htf_gate") : undefined,
        use_htf_gate: wfUseHtfGate,
        htf_timeframes: wfUseHtfGate ? wfHtfTfs : undefined,
      });
      if (r.error) { setWfError(r.error); return; }
      setWfRunId(r.id);
      setWfStatus("running");
      stopPolling();
      wfPollingActiveRef.current = true;

      // Self-scheduling poll: only queues the NEXT check after the current
      // one fully resolves, so a slow request can never overlap with
      // another in-flight one — fixes a real race condition the old fixed
      // setInterval(400ms) had if any single request took longer than
      // 400ms to come back (slow network, long simulation, dev server
      // hot-reload mid-flight, etc.).
      const poll = async () => {
        if (!wfPollingActiveRef.current) return;
        try {
          const s = await getWalkForwardStatus(r.id);
          if (!wfPollingActiveRef.current) return; // cancelled while awaiting
          setWfProgress(s.progress || 0);
          if (s.status === "done" || s.status === "error" || s.status === "stopped") {
            stopPolling();
            setWfStatus(s.status);
            if (s.status === "error") { setWfError(s.error || "unknown error"); return; }
            const res = await getWalkForwardResult(r.id);
            if (res.error) setWfError(res.error);
            else { setWfResult(res); refreshWfLog(); }
            return;
          }
        } catch (e) {
          // A single missed poll shouldn't kill the whole run — the
          // simulation keeps running server-side regardless. Retry a few
          // times before actually giving up.
          wfPollRetriesRef.current += 1;
          if (wfPollRetriesRef.current > 5) {
            stopPolling();
            setWfError("Lost connection while checking progress — the simulation may still be running server-side.");
            return;
          }
        }
        wfPollRef.current = setTimeout(poll, 400);
      };
      wfPollRetriesRef.current = 0;
      poll();
    } catch (e) { setWfError("request failed"); }
  };

  const stopWalkForward = async () => {
    if (!wfRunId) return;
    try { await stopWalkForwardBacktest(wfRunId); } catch (e) {}
  };

  if (!open) return null;

  const run = async () => {
    setLoading(true); setError(null);
    try {
      const d = await runBacktest({ timeframe, lookback, forward, step });
      if (d.error) setError(d.error);
      else { setResult(d); refreshLog(); }
    } catch (e) { setError("request failed"); }
    setLoading(false);
  };

  const removeEntry = async (id) => { await deleteBacktestLogEntry(id); refreshLog(); };
  const clearAll = async () => {
    if (!window.confirm("Clear the entire backtest log? This can't be undone.")) return;
    await clearBacktestLog();
    refreshLog();
  };

  const s = result?.summary;
  const netColor = s ? (s.net_return_pct >= 0 ? "#00f59b" : "#ff3b56") : "#e2e8f0";
  const curve = (result?.equity_curve || []).map((p, i) => ({ i, equity: p.equity }));

  const wfCurve = (wfResult?.equity_curve || []).map((p) => ({ ts: p.ts, equity: p.equity }));
  const wfNetColor = wfResult ? (wfResult.stats.net_pnl >= 0 ? "#00f59b" : "#ff3b56") : "#e2e8f0";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="panel max-w-4xl w-full p-5 fade-up max-h-[92vh] overflow-y-auto" onClick={(e) => e.stopPropagation()} data-testid="backtest-modal">
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="font-head font-bold text-2xl text-white tracking-wide">BACKTEST · BRAIN REPLAY</div>
            <div className="font-mono-t text-[10px] text-slate-500">Replays stored SQLite history through the Brain on {timeframe}</div>
          </div>
          <button onClick={onClose} data-testid="backtest-close" className="text-slate-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex gap-1 mb-4">
          {[["test", "Quick Test"], ["walkforward", "Full Simulation"], ["simlog", `Sim Log (${wfLog.length})`], ["log", `Quick Log (${log.length})`]].map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} data-testid={`backtest-tab-${k}`}
              className={`font-mono-t text-[11px] px-3 py-1.5 rounded-sm transition-colors ${tab === k ? "bg-cyan-500 text-black" : "bg-[#0d121b] text-slate-400 border border-[#1d2635] hover:text-white"}`}>
              {label}
            </button>
          ))}
        </div>

        {tab === "test" && (
          <>
            <div className="flex flex-wrap items-end gap-3 mb-1">
              <label className="flex flex-col gap-1">
                <span className="widget-label">Check result after (max {LIMITS.forward.max})</span>
                <input type="number" min={LIMITS.forward.min} max={LIMITS.forward.max} value={forward}
                  onChange={(e) => setForward(+e.target.value)}
                  onBlur={(e) => setForward(clamp(+e.target.value, LIMITS.forward))}
                  data-testid="backtest-forward" className="w-24 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">History candles (max {LIMITS.lookback.max})</span>
                <input type="number" min={LIMITS.lookback.min} max={LIMITS.lookback.max} value={lookback}
                  onChange={(e) => setLookback(+e.target.value)}
                  onBlur={(e) => setLookback(clamp(+e.target.value, LIMITS.lookback))}
                  data-testid="backtest-lookback" className="w-24 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">Skip every (max {LIMITS.step.max})</span>
                <input type="number" min={LIMITS.step.min} max={LIMITS.step.max} value={step}
                  onChange={(e) => setStep(+e.target.value)}
                  onBlur={(e) => setStep(clamp(+e.target.value, LIMITS.step))}
                  data-testid="backtest-step" className="w-20 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <button onClick={run} disabled={loading} data-testid="backtest-run"
                className="flex items-center gap-1.5 font-mono-t text-sm font-semibold px-4 py-1.5 rounded-sm bg-cyan-500 text-black hover:bg-cyan-400 disabled:opacity-50">
                <Play className="w-3.5 h-3.5" /> {loading ? "Running…" : "Run Backtest"}
              </button>
            </div>
            <div className="font-mono-t text-[10px] text-slate-500 mb-4 leading-relaxed">
              "Check result after {forward} candles" = whenever the Brain says LONG or SHORT, wait {forward} candles later ({(forward * tfToMinutes(timeframe) / 60).toFixed(1)}h on {timeframe}) and see if price actually moved that way — a quick directional-accuracy gut check, not a real trade simulation.
              {" "}For an actual SL/TP trade-by-trade simulation with real position sizing, use the Full Simulation tab instead.
              {" "}Every run you do here is automatically saved to the Log tab.
            </div>

            {error && <div className="font-mono-t text-xs text-rose-400 mb-3">Error: {error}</div>}

            {s && (
              <div data-testid="backtest-results">
                <div className="font-mono-t text-[10px] text-slate-500 mb-3" data-testid="backtest-actual-params">
                  Actually ran with: forward candles = <span className="text-slate-300">{s.forward_candles}</span>,
                  {" "}step = <span className="text-slate-300">{s.step}</span>,
                  {" "}evaluations = <span className="text-slate-300">{s.evaluations}</span>
                  {(s.forward_candles !== forward || s.step !== step) && (
                    <span className="text-amber-400"> — differs from what you entered; the backend clamped it to a valid range.</span>
                  )}
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mb-4">
                  <Stat label="Win rate" value={`${fmt(s.win_rate_pct, 1)}%`} color={s.win_rate_pct >= 50 ? "#00f59b" : "#ffb800"} testid="backtest-winrate" />
                  <Stat label="Net return (gross)" value={`${s.net_return_pct >= 0 ? "+" : ""}${fmt(s.net_return_pct, 2)}%`} color={netColor} testid="backtest-net" />
                  <Stat label="Directional trades" value={s.directional_trades} />
                  <Stat label="Avg / trade" value={`${fmt(s.avg_return_per_trade_pct, 3)}%`} color={s.avg_return_per_trade_pct >= 0 ? "#00f59b" : "#ff3b56"} />
                </div>

                <div className="grid grid-cols-4 gap-2 mb-4">
                  {["LONG", "SHORT", "WAIT", "AVOID"].map((st) => (
                    <div key={st} className="panel p-2 text-center">
                      <div className={`font-head font-bold text-sm ${dirStyle(st).text}`}>{st}</div>
                      <div className="font-mono-t text-lg text-slate-200">{s.state_counts[st] || 0}</div>
                    </div>
                  ))}
                </div>

                <div className="panel p-3 mb-3">
                  <div className="widget-label mb-2">Cumulative signed return (directional signals)</div>
                  <div className="h-40" data-testid="backtest-equity-curve">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={curve}>
                        <defs>
                          <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.5} />
                            <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <XAxis dataKey="i" hide />
                        <YAxis width={38} tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "JetBrains Mono" }} />
                        <ReferenceLine y={0} stroke="#475569" strokeDasharray="3 3" />
                        <Tooltip contentStyle={{ background: "#0d121b", border: "1px solid #1d2635", fontFamily: "JetBrains Mono", fontSize: 11 }} />
                        <Area type="monotone" dataKey="equity" stroke="#38bdf8" fill="url(#eq)" strokeWidth={1.5} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="flex items-start gap-2 font-mono-t text-[10px] text-amber-500/90 bg-amber-500/5 border border-amber-500/20 rounded-sm p-2">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>{s.disclaimer}</span>
                </div>
              </div>
            )}

            {!s && !loading && !error && (
              <div className="font-mono-t text-[11px] text-slate-500 py-8 text-center">
                Configure parameters and run to replay the Brain over stored history.
              </div>
            )}
          </>
        )}

        {tab === "walkforward" && (
          <div data-testid="backtest-walkforward">
            <div className="font-mono-t text-[10px] text-slate-500 mb-3 leading-relaxed">
              A real trade-by-trade simulation — same Brain, same open/flip/hold logic your live auto-trader uses, walked forward one candle at a time. Each trade actually opens with a real SL/TP and gets checked against every following candle's high/low until one is hit, just like a live trade would. Slower than Quick Test, but a far more honest picture.
            </div>

            <div className="flex flex-wrap items-end gap-3 mb-3">
              <label className="flex flex-col gap-1">
                <span className="widget-label">Days to test</span>
                <input type="number" min="1" max="60" value={wfDays} onChange={(e) => setWfDays(e.target.value)}
                  data-testid="wf-days" className="w-20 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">Start balance ($)</span>
                <input type="number" min="1" value={wfBalance} onChange={(e) => setWfBalance(e.target.value)}
                  data-testid="wf-balance" className="w-24 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">Capital / trade (%)</span>
                <input type="number" min="1" max="100" value={wfCapitalPct} onChange={(e) => setWfCapitalPct(e.target.value)}
                  data-testid="wf-capital-pct" className="w-20 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">Leverage (x)</span>
                <input type="number" min="1" max="20" value={wfLeverage} onChange={(e) => setWfLeverage(e.target.value)}
                  data-testid="wf-leverage" className="w-16 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">SL (× ATR)</span>
                <input type="number" step="0.1" min="0.1" value={wfSlMult} onChange={(e) => setWfSlMult(e.target.value)}
                  data-testid="wf-sl-mult" className="w-16 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              <label className="flex flex-col gap-1">
                <span className="widget-label">TP (× ATR)</span>
                <input type="number" step="0.1" min="0.1" value={wfTpMult} onChange={(e) => setWfTpMult(e.target.value)}
                  data-testid="wf-tp-mult" className="w-16 bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1 font-mono-t text-sm text-white" />
              </label>
              {wfStatus === "running" ? (
                <button onClick={stopWalkForward} data-testid="wf-stop"
                  className="flex items-center gap-1.5 font-mono-t text-sm font-semibold px-4 py-1.5 rounded-sm bg-rose-600 text-white hover:bg-rose-500">
                  <Square className="w-3.5 h-3.5" /> Stop
                </button>
              ) : (
                <button onClick={runWalkForward} data-testid="wf-run"
                  className="flex items-center gap-1.5 font-mono-t text-sm font-semibold px-4 py-1.5 rounded-sm bg-cyan-500 text-black hover:bg-cyan-400">
                  <Play className="w-3.5 h-3.5" /> Run Simulation
                </button>
              )}
            </div>

            {liveSettings && (
              <div className="font-mono-t text-[10px] mb-4 flex items-center gap-2 flex-wrap" data-testid="wf-about-to-test">
                <span className="text-slate-500">About to test:</span>
                {["agent_weights", "entry", "conflict", "htf_gate"].map((section) => (
                  <span key={section}
                    className={`px-2 py-0.5 rounded-sm border ${dirty[section] ? "text-amber-400 border-amber-500/50 bg-amber-500/10" : "text-slate-500 border-slate-700/50"}`}
                    data-testid={`wf-dirty-indicator-${section}`}>
                    {section === "agent_weights" ? "Weights" : section === "entry" ? "Entry" : section === "conflict" ? "Conflict" : "HTF Gate"}: {dirty[section] ? "custom" : "live"}
                  </span>
                ))}
              </div>
            )}

            {liveSettings && (
              <div className="panel p-3 mb-4" data-testid="wf-weights-editor">
                <div className="flex items-center justify-between mb-2">
                  <div className="widget-label">
                    Engine weights {isCustomWeights ? <span className="text-amber-400">(testing custom — not live)</span> : <span className="text-slate-500">(using your current live weights)</span>}
                  </div>
                  {isCustomWeights && (
                    <button onClick={resetToLiveWeights} data-testid="wf-weights-reset"
                      className="font-mono-t text-[10px] text-slate-400 hover:text-white underline">
                      Reset to live weights
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                  {Object.entries(currentWeights).map(([id, w]) => (
                    <label key={id} className="flex flex-col gap-0.5">
                      <span className="font-mono-t text-[9px] text-slate-500 truncate">{id}</span>
                      <input type="number" step="0.01" min={WEIGHT_LIMITS.min} max={WEIGHT_LIMITS.max} value={w}
                        onChange={(e) => setWeightField(id, e.target.value)}
                        onBlur={(e) => clampWeightField(id, e.target.value)}
                        data-testid={`wf-weight-${id}`}
                        className="font-mono-t text-xs text-white bg-[#0d121b] border border-[#1d2635] rounded-sm px-1.5 py-1" />
                    </label>
                  ))}
                </div>
                <div className="font-mono-t text-[10px] text-slate-500 mt-2">
                  Edit a weight to test a hypothetical change for this run only (clamped 0-{WEIGHT_LIMITS.max}, same limit as live Settings — a weight this high would let one engine override the other 9) — your live/paper trading keeps using the real weights until you explicitly click "Save as Live" on a result below.
                </div>
              </div>
            )}

            {liveSettings && (["entry", "conflict", "htf_gate"]).map((section) => {
              const title = section === "entry" ? "Entry Thresholds"
                : section === "conflict" ? "Conflict / Contradiction"
                : "HTF Gate values";
              const values = currentValues(section);
              const custom = isCustom(section);
              return (
                <div className="panel p-3 mb-4" key={section} data-testid={`wf-${section}-editor`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="widget-label">
                      {title} {custom ? <span className="text-amber-400">(testing custom — not live)</span> : <span className="text-slate-500">(using your current live values)</span>}
                    </div>
                    {custom && (
                      <button onClick={() => resetSection(section)} data-testid={`wf-${section}-reset`}
                        className="font-mono-t text-[10px] text-slate-400 hover:text-white underline">
                        Reset to live values
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                    {SECTION_FIELDS[section].map(([key, label]) => (
                      <label key={key} className="flex flex-col gap-0.5">
                        <span className="font-mono-t text-[9px] text-slate-500">{label}</span>
                        <input type="number" step="0.1" value={values[key] ?? ""}
                          onChange={(e) => setField(section, key, e.target.value)}
                          data-testid={`wf-${section}-${key}`}
                          className="font-mono-t text-xs text-white bg-[#0d121b] border border-[#1d2635] rounded-sm px-1.5 py-1" />
                      </label>
                    ))}
                  </div>
                  <div className="font-mono-t text-[10px] text-slate-500 mt-2">
                    Same panel as Settings → {title} — this is exactly what the backtest already reads live; editing here only tests a hypothetical change for this run, never touches live/paper trading unless you click "Save as Live" below. No bounds enforced here (unlike weights) since valid ranges vary a lot per field — double-check values make sense before saving.
                  </div>
                </div>
              );
            })}

            <div className="panel p-3 mb-4" data-testid="wf-htf-controls">
              <div className="flex items-center justify-between mb-2">
                <div className="widget-label">HTF Regime Gate</div>
                <button onClick={() => setWfUseHtfGate((v) => !v)} data-testid="wf-htf-toggle"
                  className={`font-mono-t text-[10px] px-3 py-1 rounded-full border transition-colors ${wfUseHtfGate ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-400" : "bg-slate-700/30 border-slate-600/50 text-slate-400"}`}>
                  {wfUseHtfGate ? "ON" : "OFF"}
                </button>
              </div>
              {wfUseHtfGate ? (
                <>
                  <div className="flex flex-wrap gap-2 mb-2">
                    {HTF_CHOICES.map((tf) => (
                      <label key={tf} className="flex items-center gap-1.5 font-mono-t text-xs text-slate-300 cursor-pointer">
                        <input type="checkbox" checked={wfHtfTfs.includes(tf)} onChange={() => toggleHtfTf(tf)}
                          data-testid={`wf-htf-tf-${tf}`} />
                        {tf}
                      </label>
                    ))}
                  </div>
                  <div className="font-mono-t text-[10px] text-slate-500">
                    Your live system uses 4h + 1d by default. Checking others here (30m, 1h) tests a different regime definition — independent of your live config, this run only.
                    {wfHtfTfs.length === 0 && <span className="text-amber-400"> Select at least one, or it'll fall back to 4h + 1d.</span>}
                  </div>
                </>
              ) : (
                <div className="font-mono-t text-[10px] text-slate-500">
                  Gate disabled for this run — every entry is judged purely on the {timeframe} signal, with no higher-timeframe trend filter at all. Useful for seeing how much the gate is actually helping (or hurting).
                </div>
              )}
            </div>

            {wfStatus === "running" && (
              <div className="mb-4" data-testid="wf-progress">
                <div className="h-1.5 bg-[#0d121b] rounded-full overflow-hidden border border-[#1d2635]">
                  <div className="h-full bg-cyan-500 transition-all" style={{ width: `${Math.round(wfProgress * 100)}%` }} />
                </div>
                <div className="font-mono-t text-[10px] text-slate-500 mt-1">Simulating bar-by-bar… {Math.round(wfProgress * 100)}%</div>
              </div>
            )}

            {wfError && <div className="font-mono-t text-xs text-rose-400 mb-3">Error: {wfError}</div>}

            {wfResult && (
              <div data-testid="wf-results">
                {wfResult.data_shortfall && (
                  <div className="flex items-start gap-2 font-mono-t text-xs text-rose-400 bg-rose-500/10 border border-rose-500/40 rounded-sm p-3 mb-3" data-testid="wf-data-shortfall-warning">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                    <span>
                      <strong>You asked for {wfResult.config.days} days, but only {wfResult.actual_days_tested} days of {wfResult.timeframe} history are actually stored.</strong> This result is based on a much shorter window than requested — treat it as low-confidence until more candle history has synced. Check the "Show Data Folder" tray option or let the app run longer to accumulate more history.
                    </span>
                  </div>
                )}
                <div className="flex items-center justify-between mb-3">
                  <div className="font-mono-t text-[10px] text-slate-500">
                    This run used {wfResult.config.used_custom_weights ? <span className="text-amber-400">custom weights</span> : <span className="text-slate-300">live weights</span>}
                    {(wfResult.config.used_custom_entry || wfResult.config.used_custom_conflict || wfResult.config.used_custom_htf_gate_values) && (
                      <>, custom {[
                        wfResult.config.used_custom_entry && "entry",
                        wfResult.config.used_custom_conflict && "conflict",
                        wfResult.config.used_custom_htf_gate_values && "HTF gate values",
                      ].filter(Boolean).join(" + ")}</>
                    )}
                    {" "}· HTF gate {wfResult.config.use_htf_gate ? <span className="text-emerald-400">ON ({wfResult.config.htf_timeframes.join(", ")})</span> : <span className="text-slate-400">OFF</span>}
                    {" "}· tested {wfResult.actual_days_tested} of {wfResult.config.days} requested days.
                  </div>
                  {(wfResult.config.used_custom_weights || wfResult.config.used_custom_entry || wfResult.config.used_custom_conflict || wfResult.config.used_custom_htf_gate_values) && (
                    <button onClick={saveAsLive} disabled={savingWeights} data-testid="wf-save-live"
                      className="font-mono-t text-[11px] font-semibold px-3 py-1.5 rounded-sm bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-50">
                      {savingWeights ? "Saving…" : "Save These Values As Live"}
                    </button>
                  )}
                </div>
                {saveMsg && <div className="font-mono-t text-[11px] text-emerald-400 mb-3">{saveMsg}</div>}


                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mb-3">
                  <Stat label="Trades" value={wfResult.stats.trades} />
                  <Stat label="Win rate" value={`${fmt(wfResult.stats.win_rate * 100, 1)}%`} color={wfResult.stats.win_rate >= 0.5 ? "#00f59b" : "#ffb800"} />
                  <Stat label="Profit factor" value={wfResult.stats.profit_factor ?? "—"} />
                  <Stat label="Avg R" value={fmt(wfResult.stats.avg_r, 2)} color={wfResult.stats.avg_r >= 0 ? "#00f59b" : "#ff3b56"} />
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5 mb-4">
                  <Stat label="Net PnL" value={`${wfResult.stats.net_pnl >= 0 ? "+" : ""}$${fmt(wfResult.stats.net_pnl, 2)}`} color={wfNetColor} />
                  <Stat label="Return" value={`${wfResult.stats.return_pct >= 0 ? "+" : ""}${fmt(wfResult.stats.return_pct, 2)}%`} color={wfNetColor} />
                  <Stat label="Final balance" value={`$${fmt(wfResult.final_balance, 2)}`} />
                </div>

                <div className="panel p-3 mb-4">
                  <div className="widget-label mb-2">Equity curve (real $ balance over time)</div>
                  <div className="h-40" data-testid="wf-equity-curve">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={wfCurve}>
                        <defs>
                          <linearGradient id="wfeq" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor={wfResult.stats.net_pnl >= 0 ? "#00f59b" : "#ff3b56"} stopOpacity={0.4} />
                            <stop offset="100%" stopColor={wfResult.stats.net_pnl >= 0 ? "#00f59b" : "#ff3b56"} stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <XAxis dataKey="ts" hide />
                        <YAxis width={48} domain={["auto", "auto"]} tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "JetBrains Mono" }} />
                        <ReferenceLine y={wfResult.config.start_balance} stroke="#475569" strokeDasharray="3 3" />
                        <Tooltip contentStyle={{ background: "#0d121b", border: "1px solid #1d2635", fontFamily: "JetBrains Mono", fontSize: 11 }}
                          labelFormatter={(ts) => fmtTime(ts)} formatter={(v) => [`$${fmt(v, 2)}`, "Equity"]} />
                        <Area type="stepAfter" dataKey="equity" stroke={wfResult.stats.net_pnl >= 0 ? "#00f59b" : "#ff3b56"} fill="url(#wfeq)" strokeWidth={1.5} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="widget-label mb-2">Per-engine attribution — which of your 10 engines are actually earning their keep</div>
                <div className="panel overflow-x-auto mb-4" data-testid="wf-attribution">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="widget-label border-b border-[#1d2635]">
                        <th className="px-2 py-2">Engine</th>
                        <th className="px-2 py-2">Led (n)</th>
                        <th className="px-2 py-2">Lead WR</th>
                        <th className="px-2 py-2">Agree W/L</th>
                        <th className="px-2 py-2">Edge</th>
                        <th className="px-2 py-2">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(wfResult.attribution).map(([key, a]) => (
                        <tr key={key} className="border-b border-[#161f2e] font-mono-t text-[11px]" data-testid={`wf-attr-row-${key}`}>
                          <td className="px-2 py-2 text-slate-200">{a.engine}</td>
                          <td className="px-2 py-2 text-slate-400">{a.strongest_count}</td>
                          <td className="px-2 py-2 text-slate-300">{a.strongest_win_rate == null ? "—" : `${fmt(a.strongest_win_rate * 100, 0)}%`}</td>
                          <td className="px-2 py-2 text-slate-400">{fmt(a.agreement_on_wins * 100, 0)}% / {fmt(a.agreement_on_losses * 100, 0)}%</td>
                          <td className="px-2 py-2" style={{ color: a.edge > 0 ? "#00f59b" : a.edge < 0 ? "#ff3b56" : "#94a3b8" }}>
                            {a.edge >= 0 ? "+" : ""}{fmt(a.edge * 100, 1)}%
                          </td>
                          <td className="px-2 py-2">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded-sm border ${RECOMMENDATION_STYLE[a.recommendation] || RECOMMENDATION_STYLE.keep}`}>
                              {a.recommendation}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="font-mono-t text-[10px] text-slate-500 mb-4">
                  "Edge" = how often an engine agreed with winning trades minus how often it agreed with losing trades. Positive and consistent = genuinely adding value. Needs at least 10 closed trades before it'll recommend anything — below that it says "insufficient data" rather than guess.
                </div>

                <div className="panel overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="widget-label border-b border-[#1d2635]">
                        <th className="px-2 py-2">Side</th>
                        <th className="px-2 py-2">Entry</th>
                        <th className="px-2 py-2">Exit</th>
                        <th className="px-2 py-2">Reason</th>
                        <th className="px-2 py-2">R</th>
                        <th className="px-2 py-2">PnL</th>
                        <th className="px-2 py-2">Led by</th>
                      </tr>
                    </thead>
                    <tbody>
                      {wfResult.trades.map((t) => {
                        const st = dirStyle(t.direction);
                        return (
                          <tr key={t.id} className="border-b border-[#161f2e] font-mono-t text-[11px]" data-testid={`wf-trade-${t.id}`}>
                            <td className={`px-2 py-2 font-bold ${st.text}`}>{t.direction}</td>
                            <td className="px-2 py-2 text-slate-300">${fmt(t.entry_price, 1)}</td>
                            <td className="px-2 py-2 text-slate-300">${fmt(t.exit_price, 1)}</td>
                            <td className="px-2 py-2 text-slate-500">{t.exit_reason}</td>
                            <td className="px-2 py-2" style={{ color: t.r_multiple >= 0 ? "#00f59b" : "#ff3b56" }}>{t.r_multiple >= 0 ? "+" : ""}{fmt(t.r_multiple, 2)}</td>
                            <td className="px-2 py-2" style={{ color: t.pnl >= 0 ? "#00f59b" : "#ff3b56" }}>{t.pnl >= 0 ? "+" : ""}${fmt(t.pnl, 2)}</td>
                            <td className="px-2 py-2 text-slate-400">{t.strongest_engine || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="flex items-start gap-2 font-mono-t text-[10px] text-amber-500/90 bg-amber-500/5 border border-amber-500/20 rounded-sm p-2 mt-3">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>Simplified vs a full production backtest: single position at a time (no layering), no spread/slippage modeled, no news-event filter. Same-bar SL+TP ambiguity always resolves as the stop hitting first (the pessimistic assumption, on purpose).</span>
                </div>
              </div>
            )}

            {!wfResult && wfStatus !== "running" && !wfError && (
              <div className="font-mono-t text-[11px] text-slate-500 py-8 text-center">
                Configure parameters and run a real bar-by-bar trade simulation.
              </div>
            )}
          </div>
        )}

        {tab === "simlog" && (
          <div data-testid="wf-log">
            <div className="flex items-center justify-between mb-2">
              <div className="font-mono-t text-[10px] text-slate-500">
                Every Full Simulation run gets saved here automatically, most recent first — including which weights it used.
              </div>
              {wfLog.length > 0 && (
                <button onClick={clearWfLogAll} data-testid="wf-log-clear"
                  className="font-mono-t text-[10px] px-2 py-1 rounded-sm border border-rose-900/50 text-rose-400 hover:bg-rose-950/40 flex items-center gap-1 flex-shrink-0 ml-3">
                  <Trash2 className="w-3 h-3" /> Clear all
                </button>
              )}
            </div>

            {wfLogLoading && <div className="font-mono-t text-[11px] text-slate-500 py-4 text-center">Loading…</div>}

            {!wfLogLoading && wfLog.length === 0 && (
              <div className="font-mono-t text-[11px] text-slate-500 py-8 text-center">
                No Full Simulation runs logged yet — run one from the "Full Simulation" tab.
              </div>
            )}

            {!wfLogLoading && wfLog.length > 0 && (
              <div className="panel overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="widget-label border-b border-[#1d2635]">
                      <th className="px-2 py-2">When</th>
                      <th className="px-2 py-2">TF</th>
                      <th className="px-2 py-2">Days</th>
                      <th className="px-2 py-2">Cap%</th>
                      <th className="px-2 py-2">Lev</th>
                      <th className="px-2 py-2">SL/TP</th>
                      <th className="px-2 py-2">HTF Gate</th>
                      <th className="px-2 py-2">Trades</th>
                      <th className="px-2 py-2">Win %</th>
                      <th className="px-2 py-2">PF</th>
                      <th className="px-2 py-2">Net $</th>
                      <th className="px-2 py-2">Return %</th>
                      <th className="px-2 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {wfLog.map((r) => {
                      const expanded = expandedLogId === r.id;
                      const customCount = [r.used_custom_weights, r.used_custom_entry, r.used_custom_conflict, r.used_custom_htf_gate_values].filter(Boolean).length;
                      return (
                        <React.Fragment key={r.id}>
                          <tr className="border-b border-[#161f2e] font-mono-t text-[11px] cursor-pointer hover:bg-white/[0.03]"
                            onClick={() => setExpandedLogId(expanded ? null : r.id)} data-testid={`wf-log-row-${r.id}`}>
                            <td className="px-2 py-2 text-slate-500 whitespace-nowrap">
                              <span className="inline-block w-3 text-slate-600">{expanded ? "▾" : "▸"}</span> {fmtTime(r.created_at)}
                            </td>
                            <td className="px-2 py-2 text-slate-300">{r.timeframe}</td>
                            <td className="px-2 py-2 text-slate-300">
                              {r.data_shortfall ? (
                                <span className="text-rose-400 flex items-center gap-1" title={`Requested ${fmt(r.days, 1)}d, only ${fmt(r.actual_days_tested, 1)}d available`}>
                                  <AlertTriangle className="w-3 h-3" /> {fmt(r.actual_days_tested, 1)}/{fmt(r.days, 1)}
                                </span>
                              ) : fmt(r.days, 1)}
                            </td>
                            <td className="px-2 py-2 text-slate-300">{r.capital_pct}%</td>
                            <td className="px-2 py-2 text-slate-300">{r.leverage}x</td>
                            <td className="px-2 py-2 text-slate-300">{fmt(r.sl_atr_mult, 2)}/{fmt(r.tp_atr_mult, 2)}</td>
                            <td className="px-2 py-2 text-slate-400">
                              {r.use_htf_gate ? (r.htf_timeframes || []).join("/") || "on" : "off"}
                            </td>
                            <td className="px-2 py-2 text-slate-300">{r.trades}</td>
                            <td className="px-2 py-2" style={{ color: r.win_rate >= 0.5 ? "#00f59b" : "#ffb800" }}>{fmt(r.win_rate * 100, 1)}%</td>
                            <td className="px-2 py-2 text-slate-300">{r.profit_factor ?? "—"}</td>
                            <td className="px-2 py-2" style={{ color: r.net_pnl >= 0 ? "#00f59b" : "#ff3b56" }}>{r.net_pnl >= 0 ? "+" : ""}${fmt(r.net_pnl, 2)}</td>
                            <td className="px-2 py-2" style={{ color: r.return_pct >= 0 ? "#00f59b" : "#ff3b56" }}>{r.return_pct >= 0 ? "+" : ""}{fmt(r.return_pct, 2)}%</td>
                            <td className="px-2 py-2">
                              <button onClick={(e) => { e.stopPropagation(); removeWfLogEntry(r.id); }} data-testid={`wf-log-delete-${r.id}`} className="text-slate-600 hover:text-rose-400">
                                <Trash2 className="w-3 h-3" />
                              </button>
                            </td>
                          </tr>
                          {expanded && (
                            <tr className="bg-[#0a0e15]" data-testid={`wf-log-detail-${r.id}`}>
                              <td colSpan="13" className="px-4 py-3">
                                <div className="font-mono-t text-[10px] text-slate-400 mb-2">
                                  {customCount === 0
                                    ? "This run used your live settings for everything — no customization."
                                    : `This run customized ${customCount} of 4 settings sections:`}
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                                  <div className={`rounded-sm border p-2 ${r.used_custom_weights ? "border-amber-500/40 bg-amber-500/5" : "border-slate-700/40"}`}>
                                    <div className="widget-label mb-1">Weights {r.used_custom_weights ? <span className="text-amber-400">(custom)</span> : <span className="text-slate-600">(live)</span>}</div>
                                    {Object.entries(r.weights_used || {}).map(([k, v]) => (
                                      <div key={k} className="flex justify-between font-mono-t text-[10px] text-slate-300">
                                        <span className="text-slate-500">{k}</span><span>{fmt(v, 3)}</span>
                                      </div>
                                    ))}
                                  </div>
                                  <div className={`rounded-sm border p-2 ${r.used_custom_entry ? "border-amber-500/40 bg-amber-500/5" : "border-slate-700/40"}`}>
                                    <div className="widget-label mb-1">Entry {r.used_custom_entry ? <span className="text-amber-400">(custom)</span> : <span className="text-slate-600">(live)</span>}</div>
                                    {Object.keys(r.entry_used || {}).length === 0 ? (
                                      <div className="font-mono-t text-[10px] text-slate-600">Not recorded (older run)</div>
                                    ) : Object.entries(r.entry_used).map(([k, v]) => (
                                      <div key={k} className="flex justify-between font-mono-t text-[10px] text-slate-300">
                                        <span className="text-slate-500">{k}</span><span>{fmt(v, 2)}</span>
                                      </div>
                                    ))}
                                  </div>
                                  <div className={`rounded-sm border p-2 ${r.used_custom_conflict ? "border-amber-500/40 bg-amber-500/5" : "border-slate-700/40"}`}>
                                    <div className="widget-label mb-1">Conflict {r.used_custom_conflict ? <span className="text-amber-400">(custom)</span> : <span className="text-slate-600">(live)</span>}</div>
                                    {Object.keys(r.conflict_used || {}).length === 0 ? (
                                      <div className="font-mono-t text-[10px] text-slate-600">Not recorded (older run)</div>
                                    ) : Object.entries(r.conflict_used).map(([k, v]) => (
                                      <div key={k} className="flex justify-between font-mono-t text-[10px] text-slate-300">
                                        <span className="text-slate-500">{k}</span><span>{fmt(v, 2)}</span>
                                      </div>
                                    ))}
                                  </div>
                                  <div className={`rounded-sm border p-2 ${r.used_custom_htf_gate_values ? "border-amber-500/40 bg-amber-500/5" : "border-slate-700/40"}`}>
                                    <div className="widget-label mb-1">HTF Gate values {r.used_custom_htf_gate_values ? <span className="text-amber-400">(custom)</span> : <span className="text-slate-600">(live)</span>}</div>
                                    {Object.keys(r.htf_gate_used || {}).length === 0 ? (
                                      <div className="font-mono-t text-[10px] text-slate-600">Not recorded (older run)</div>
                                    ) : Object.entries(r.htf_gate_used).map(([k, v]) => (
                                      <div key={k} className="flex justify-between font-mono-t text-[10px] text-slate-300">
                                        <span className="text-slate-500">{k}</span><span>{fmt(v, 2)}</span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {wfLog.length >= 3 && (
              <div className="flex items-start gap-2 font-mono-t text-[10px] text-cyan-400/90 bg-cyan-500/5 border border-cyan-500/20 rounded-sm p-2 mt-3">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>Same rule as the Quick Test log: look for consistency across runs with similar settings, not the single best-looking one — especially when comparing "custom" weight experiments against your "live" baseline.</span>
              </div>
            )}
          </div>
        )}

        {tab === "log" && (
          <div data-testid="backtest-log">
            <div className="flex items-center justify-between mb-2">
              <div className="font-mono-t text-[10px] text-slate-500">
                Every Quick Test run gets saved here automatically, most recent first — use this to compare settings instead of screenshotting each result.
              </div>
              {log.length > 0 && (
                <button onClick={clearAll} data-testid="backtest-log-clear"
                  className="font-mono-t text-[10px] px-2 py-1 rounded-sm border border-rose-900/50 text-rose-400 hover:bg-rose-950/40 flex items-center gap-1 flex-shrink-0 ml-3">
                  <Trash2 className="w-3 h-3" /> Clear all
                </button>
              )}
            </div>

            {logLoading && <div className="font-mono-t text-[11px] text-slate-500 py-4 text-center">Loading…</div>}

            {!logLoading && log.length === 0 && (
              <div className="font-mono-t text-[11px] text-slate-500 py-8 text-center">
                No backtest runs logged yet — run one from the "Quick Test" tab.
              </div>
            )}

            {!logLoading && log.length > 0 && (
              <div className="panel overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="widget-label border-b border-[#1d2635]">
                      <th className="px-2 py-2">When</th>
                      <th className="px-2 py-2">TF</th>
                      <th className="px-2 py-2">Fwd</th>
                      <th className="px-2 py-2">Hist</th>
                      <th className="px-2 py-2">Step</th>
                      <th className="px-2 py-2">L/S/W</th>
                      <th className="px-2 py-2">Trades</th>
                      <th className="px-2 py-2">Win %</th>
                      <th className="px-2 py-2">Net %</th>
                      <th className="px-2 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {log.map((r) => (
                      <tr key={r.id} className="border-b border-[#161f2e] font-mono-t text-[11px]" data-testid={`backtest-log-row-${r.id}`}>
                        <td className="px-2 py-2 text-slate-500 whitespace-nowrap">{fmtTime(r.created_at)}</td>
                        <td className="px-2 py-2 text-slate-300">{r.timeframe}</td>
                        <td className="px-2 py-2 text-slate-300">{r.forward_candles}</td>
                        <td className="px-2 py-2 text-slate-300">{r.lookback}</td>
                        <td className="px-2 py-2 text-slate-300">{r.step}</td>
                        <td className="px-2 py-2 text-slate-400">{r.long_count}/{r.short_count}/{r.wait_count}</td>
                        <td className="px-2 py-2 text-slate-300">{r.directional_trades}</td>
                        <td className="px-2 py-2" style={{ color: r.win_rate_pct >= 50 ? "#00f59b" : "#ffb800" }}>{fmt(r.win_rate_pct, 1)}%</td>
                        <td className="px-2 py-2" style={{ color: r.net_return_pct >= 0 ? "#00f59b" : "#ff3b56" }}>{r.net_return_pct >= 0 ? "+" : ""}{fmt(r.net_return_pct, 2)}%</td>
                        <td className="px-2 py-2">
                          <button onClick={() => removeEntry(r.id)} data-testid={`backtest-log-delete-${r.id}`} className="text-slate-600 hover:text-rose-400">
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {log.length >= 3 && (
              <div className="flex items-start gap-2 font-mono-t text-[10px] text-cyan-400/90 bg-cyan-500/5 border border-cyan-500/20 rounded-sm p-2 mt-3">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>Look for consistency across rows, not the single best-looking one. A win rate that swings wildly between runs with different settings is a sign of sensitivity, not a strong edge.</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};