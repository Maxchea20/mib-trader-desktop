import React, { useEffect, useState, useCallback } from "react";
import { TrendingUp, TrendingDown, Wallet, Bot, Plus } from "lucide-react";
import {
  openPaperTrade, closePaperTrade, getPaperTrades, getPaperStats,
  updateAutotrade, getMexcAccount,
} from "../../lib/api";
import { dirStyle, fmt } from "../../lib/style";
import { TradeTable } from "./TradeMoneyRow";

const LiveTradingControls = ({ auto, onSave }) => {
  const [acct, setAcct] = useState(null);
  useEffect(() => {
    let active = true;
    let timeoutId = null;
    const tick = async () => {
      if (!active) return;
      try {
        const a = await getMexcAccount();
        if (active) setAcct(a);
      } catch (e) {
        if (active) setAcct({ connected: false, error: e?.response?.data?.error || e?.message || "request failed" });
      }
      if (active) timeoutId = setTimeout(tick, 5000);
    };
    tick();
    return () => { active = false; if (timeoutId) clearTimeout(timeoutId); };
  }, []);
  const cfg = auto?.config || {};
  const sizing = auto?.sizing_preview || {};
  const sizingMode = cfg.sizing_mode || "NORMAL";
  const armed = !!auto?.live_armed;
  const entryEngineLabel = cfg.entry_engine === "scenario" ? "SCENARIO ENGINE" : "HUNT C-FI";
  const [allocationInput, setAllocationInput] = useState(cfg.allocation_pct ?? 20);
  const [maxNotionalInput, setMaxNotionalInput] = useState(cfg.max_live_notional_usd ?? 1000);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => { if (cfg.allocation_pct != null) setAllocationInput(cfg.allocation_pct); }, [cfg.allocation_pct]);
  useEffect(() => { if (cfg.max_live_notional_usd != null) setMaxNotionalInput(cfg.max_live_notional_usd); }, [cfg.max_live_notional_usd]);
  const connected = acct?.connected;
  const leverage = cfg.leverage ?? 10;
  const minLev = sizing.min_leverage;
  const maxLev = sizing.max_leverage;
  const saveField = async (field, value, setLocal) => {
    const a = await onSave({ [field]: value });
    if (a?.config && a.config[field] != null) setLocal(a.config[field]);
  };
  const doRefresh = async () => {
    setRefreshing(true);
    try { await onSave({ refresh_normal_base: true }); } finally { setRefreshing(false); }
  };
  return (
    <div className="panel mb-3 p-3" data-testid="live-trading-controls">
      <div className="flex items-center justify-between mb-3">
        <div className="font-head font-bold text-slate-200 tracking-wide">LIVE AUTO-TRADE CONTROLS</div>
        <span className={`font-mono-t text-[10px] px-2 py-1 rounded-sm border ${armed ? "text-amber-400 border-amber-500/40 bg-amber-500/5" : "text-slate-400 border-[#1d2635] bg-[#0d121b]"}`}>
          {armed ? "REAL ACCOUNT · ARMED" : "REAL ACCOUNT · NOT ARMED"}
        </span>
      </div>
      {!armed && (
        <div className="mb-3 px-2 py-1.5 border border-amber-500/40 bg-amber-500/5 font-mono-t text-[11px] text-amber-300">No real order until .env has MEXC_LIVE_TRADING_ENABLED=true and you restart.</div>
      )}
      {acct && !connected && (
        <div className="mb-3 px-2 py-1.5 border border-rose-500/40 bg-rose-500/5 font-mono-t text-[11px] text-rose-300">MEXC account not connected: {acct.error || "unknown error"}</div>
      )}
      {sizing.error && (
        <div className="mb-3 px-2 py-1.5 border border-rose-500/40 bg-rose-500/5 font-mono-t text-[11px] text-rose-300">Sizing not currently valid: {sizing.error}</div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">ACCOUNT BALANCE</div><div className="font-mono-t text-lg text-slate-100">{connected ? `${fmt(acct.equity, 2)} USDT` : "? USDT"}</div></div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">AVAILABLE</div><div className="font-mono-t text-lg text-slate-100">{connected ? `${fmt(acct.available_balance, 2)} USDT` : "? USDT"}</div></div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">UNREALIZED PNL</div><div className="font-mono-t text-lg">{connected ? <Pnl v={acct.unrealized_pnl} /> : <span className="text-slate-400">?</span>}</div></div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">MARGIN</div><div className="font-mono-t text-lg text-slate-100">Isolated</div></div>
      </div>
      <div className="font-head font-bold text-slate-200 tracking-wide text-sm mb-2">POSITION SIZING</div>
      <div className="flex gap-1 mb-3">
        {["NORMAL", "COMPOUNDING"].map((m) => (
          <button key={m} onClick={() => onSave({ sizing_mode: m })} className={`px-3 py-1.5 border font-mono-t text-[11px] rounded-sm ${sizingMode === m ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1d2635] bg-[#0d121b] text-slate-400"}`}>
            {m === "NORMAL" ? "NORMAL TRADE" : "COMPOUNDING"}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        {sizingMode === "NORMAL" ? (
          <div>
            <div className="widget-label mb-1">NORMAL BASE</div>
            <div className="flex items-center gap-2">
              <div className="flex-1 px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] font-mono-t text-xs text-slate-300 rounded-sm">{sizing.normal_base != null ? `$${fmt(sizing.normal_base, 2)}` : "—"}</div>
              <button onClick={doRefresh} disabled={refreshing} className="px-2 py-1.5 border border-[#1d2635] bg-[#0d121b] text-slate-300 font-mono-t text-[11px] rounded-sm">{refreshing ? "…" : "Refresh"}</button>
            </div>
          </div>
        ) : (
          <div>
            <div className="widget-label mb-1">ACCOUNT BALANCE (LIVE)</div>
            <div className="px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] font-mono-t text-xs text-slate-300 rounded-sm">{sizing.balance_used != null ? `$${fmt(sizing.balance_used, 2)}` : "?"}</div>
          </div>
        )}
        <div>
          <div className="widget-label mb-1">ALLOCATION</div>
          <div className="flex items-center gap-2">
            <input type="number" min="0.1" max="100" step="0.1" value={allocationInput} onChange={(e) => setAllocationInput(e.target.value)} onBlur={() => saveField("allocation_pct", Number(allocationInput), setAllocationInput)} className="w-full px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] text-slate-200 font-mono-t text-xs rounded-sm" />
            <span className="font-mono-t text-xs text-slate-400">%</span>
          </div>
        </div>
        <div>
          <div className="widget-label mb-1">LEVERAGE</div>
          <div className="flex gap-1">
            {[10, 20, 50, 100].map((x) => {
              const outOfBounds = (minLev != null && x < minLev) || (maxLev != null && x > maxLev);
              return (
                <button key={x} onClick={() => !outOfBounds && saveField("leverage", x, () => {})} disabled={outOfBounds} className={`flex-1 px-2 py-1.5 border font-mono-t text-[10px] rounded-sm ${outOfBounds ? "border-[#1d2635] text-slate-600 opacity-50" : Number(leverage) === x ? "border-amber-500/60 bg-amber-500/10 text-amber-300" : "border-[#1d2635] text-slate-400"}`}>{x}x</button>
              );
            })}
          </div>
        </div>
        <div>
          <div className="widget-label mb-1">MAX NOTIONAL (SAFETY CAP)</div>
          <input type="number" min="1" value={maxNotionalInput} onChange={(e) => setMaxNotionalInput(e.target.value)} onBlur={() => saveField("max_live_notional_usd", Number(maxNotionalInput), setMaxNotionalInput)} className="w-full px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] text-slate-200 font-mono-t text-xs rounded-sm" />
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">CALCULATED CAPITAL</div><div className="font-mono-t text-base text-slate-100">{sizing.calculated_capital != null ? `$${fmt(sizing.calculated_capital, 2)}` : "—"}</div></div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">CALCULATED NOTIONAL</div><div className="font-mono-t text-base text-slate-100">{sizing.calculated_notional != null ? `$${fmt(sizing.calculated_notional, 2)}` : "—"}</div></div>
        <div className={`border p-2 ${sizing.capped ? "border-amber-500/50 bg-amber-500/5" : "border-[#1d2635] bg-[#0d121b]"}`}><div className="widget-label">FINAL NOTIONAL</div><div className={`font-mono-t text-base ${sizing.capped ? "text-amber-300" : "text-slate-100"}`}>{sizing.final_notional != null ? `$${fmt(sizing.final_notional, 2)}` : "—"}</div></div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2"><div className="widget-label">CONTRACT QUANTITY</div><div className="font-mono-t text-base text-slate-100">{sizing.final_quantity != null ? fmt(sizing.final_quantity, 0) : "—"}</div></div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-1">
        <div>
          <div className="widget-label mb-1">STOP LOSS (HUNT · PNL)</div>
          <div className="px-2 py-1.5 bg-[#0d121b] border border-rose-500/30 font-mono-t text-xs text-rose-300 rounded-sm">{auto?.sl_pnl_usd != null ? `${auto.sl_pnl_usd >= 0 ? "+" : ""}${fmt(auto.sl_pnl_usd, 2)} USDT` : "waiting for Hunt levels"}{auto?.sl_price != null ? `  @ ${fmt(auto.sl_price, 1)}` : ""}</div>
        </div>
        <div>
          <div className="widget-label mb-1">TAKE PROFIT (HUNT · PNL)</div>
          <div className="px-2 py-1.5 bg-[#0d121b] border border-emerald-500/30 font-mono-t text-xs text-emerald-300 rounded-sm">{auto?.tp_pnl_usd != null ? `${auto.tp_pnl_usd >= 0 ? "+" : ""}${fmt(auto.tp_pnl_usd, 2)} USDT` : "waiting for Hunt levels"}{auto?.tp_price != null ? `  @ ${fmt(auto.tp_price, 1)}` : ""}</div>
        </div>
        <div>
          <div className="widget-label mb-1">EXECUTION</div>
          <div className="px-2 py-1.5 bg-[#0d121b] border border-amber-500/30 font-mono-t text-xs text-amber-400 rounded-sm">{entryEngineLabel} · ISOLATED {fmt(leverage, 0)}x</div>
        </div>
      </div>
    </div>
  );
};

const Pnl = ({ v, pct }) => {
  if (v === null || v === undefined) return <span className="text-slate-500">—</span>;
  const c = v >= 0 ? "text-emerald-400" : "text-rose-400";
  return <span className={`font-mono-t ${c}`}>{v >= 0 ? "+" : ""}${fmt(v, 2)} {pct != null && <span className="text-[10px] opacity-70">({v >= 0 ? "+" : ""}{fmt(pct, 2)}%)</span>}</span>;
};

export const PaperTradingPanel = ({ brain, livePrice, timeframe, auto, onSaveAuto }) => {
  const [trades, setTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [markPrice, setMarkPrice] = useState(null);
  const [tab, setTab] = useState("open");
  const [showForm, setShowForm] = useState(false);
  const bias = brain?.direction && brain.direction !== "NEUTRAL" ? brain.direction : "LONG";
  const [side, setSide] = useState(bias);
  const [notional, setNotional] = useState(1000);
  const [slPct, setSlPct] = useState(1.0);
  const [tpPct, setTpPct] = useState(2.0);
  const [busy, setBusy] = useState(false);
  const entry = livePrice || markPrice;
  const slPrice = entry ? (side === "LONG" ? entry * (1 - slPct / 100) : entry * (1 + slPct / 100)) : null;
  const tpPrice = entry ? (side === "LONG" ? entry * (1 + tpPct / 100) : entry * (1 - tpPct / 100)) : null;
  const refresh = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([getPaperTrades(), getPaperStats()]);
      setTrades(d.trades || []);
      setMarkPrice(d.live_price);
      setStats(s);
    } catch (e) {}
  }, []);
  useEffect(() => {
    let active = true;
    let timeoutId = null;
    const tick = async () => {
      if (!active) return;
      try { await refresh(); } catch (e) {}
      if (active) timeoutId = setTimeout(tick, 3000);
    };
    tick();
    return () => { active = false; if (timeoutId) clearTimeout(timeoutId); };
  }, [refresh]);
  const saveAuto = onSaveAuto || (async (payload) => { try { return await updateAutotrade(payload); } catch (e) { return null; } });
  const toggleAuto = async () => { await saveAuto({ enabled: !(auto?.config?.enabled) }); refresh(); };
  const submit = async () => {
    setBusy(true);
    try {
      await openPaperTrade({
        side, notional_usd: Number(notional),
        entry_price: entry || undefined,
        sl_price: entry ? slPrice : undefined,
        tp_price: entry ? tpPrice : undefined,
        timeframe, brain_state: brain?.state || "", consensus: brain?.consensus_score || 0,
        confidence: brain?.confidence || 0,
      });
      await refresh();
      setShowForm(false);
      setTab("open");
    } catch (e) {}
    setBusy(false);
  };
  const doClose = async (id) => { await closePaperTrade(id); refresh(); };
  const openTrades = trades.filter((t) => t.status === "OPEN");
  const autoOn = auto?.config?.enabled;
  const mode = auto?.config?.mode || "PAPER";
  const entryEngine = auto?.config?.entry_engine || "legacy";
  const toggleMode = async () => { await saveAuto({ mode: mode === "PAPER" ? "LIVE" : "PAPER" }); refresh(); };
  const scenarioOpenTrade = openTrades.find((t) => t.thesis?.engine === "scenario");
  return (
    <div className="mt-4" data-testid="paper-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-2 px-0.5">
        <div className="flex items-center gap-2">
          <Wallet className="w-4 h-4 text-emerald-400" />
          <span className="font-head font-bold text-slate-200 tracking-wide text-lg">{mode === "LIVE" ? (autoOn ? "LIVE AUTO-TRADE" : "LIVE TRADING") : "PAPER TRADING"}</span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={toggleAuto} className={`flex items-center gap-2 px-3 py-1.5 rounded-sm border font-mono-t text-[11px] ${autoOn ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-300" : "border-[#1d2635] bg-[#0d121b] text-slate-400"}`}>
            <Bot className={`w-3.5 h-3.5 ${autoOn ? "text-cyan-400" : "text-slate-500"}`} />
            AUTO-TRADE {autoOn ? "ON" : "OFF"}
          </button>
          <span className={`px-3 py-1.5 rounded-sm border font-mono-t text-[11px] ${entryEngine === "scenario" ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-300" : "border-[#1d2635] bg-[#0d121b] text-slate-400"}`}>
            ENGINE {entryEngine === "scenario" ? "SCENARIO ENGINE" : "HUNT C-FI"}
          </span>
          <button onClick={toggleMode} className={`flex items-center gap-2 px-3 py-1.5 rounded-sm border font-mono-t text-[11px] ${mode === "PAPER" ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-300" : "border-amber-500/60 bg-amber-500/10 text-amber-300"}`}>MODE {mode}</button>
          <button onClick={() => setShowForm((v) => !v)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm border border-[#1d2635] bg-[#0d121b] text-slate-300 font-mono-t text-[11px]"><Plus className="w-3.5 h-3.5" /> New Trade</button>
        </div>
      </div>
      {mode === "LIVE" && <LiveTradingControls auto={auto} onSave={saveAuto} />}
      {scenarioOpenTrade && (
        <div className="panel p-3 mb-3 border-cyan-500/30">
          <div className="widget-label mb-2">CURRENT POSITION — SCENARIO ENGINE</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 font-mono-t text-[11px]">
            <div>Thesis <span className="text-cyan-400">{scenarioOpenTrade.thesis?.thesis_id || "—"}</span></div>
            <div>Direction <span className={scenarioOpenTrade.side === "LONG" ? "text-emerald-400" : "text-rose-400"}>{scenarioOpenTrade.side}</span></div>
            <div>Entry <span className="text-slate-200">${fmt(scenarioOpenTrade.entry, 1)}</span></div>
            <div>SL <span className="text-rose-400/80">${fmt(scenarioOpenTrade.sl, 1)}</span></div>
            <div>TP <span className="text-emerald-400/80">${fmt(scenarioOpenTrade.tp, 1)}</span></div>
            <div>Size <span className="text-slate-200">{fmt(scenarioOpenTrade.qty, 4)}</span></div>
          </div>
        </div>
      )}
      {stats && (
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-3">
          <div className="panel p-2"><div className="widget-label">Trades</div><div className="font-mono-t text-lg text-slate-100">{stats.total_trades}</div></div>
          <div className="panel p-2"><div className="widget-label">Open</div><div className="font-mono-t text-lg text-cyan-400">{stats.open_trades}</div></div>
          <div className="panel p-2"><div className="widget-label">Win rate</div><div className="font-mono-t text-lg">{fmt(stats.win_rate_pct, 1)}%</div></div>
          <div className="panel p-2"><div className="widget-label">Realized PnL</div><div className="font-mono-t text-lg">{stats.total_realized_pnl >= 0 ? "+" : ""}${fmt(stats.total_realized_pnl, 2)}</div></div>
          <div className="panel p-2"><div className="widget-label">Best</div><div className="font-mono-t text-lg text-emerald-400">+${fmt(stats.best_trade, 2)}</div></div>
          <div className="panel p-2"><div className="widget-label">Worst</div><div className="font-mono-t text-lg text-rose-400">${fmt(stats.worst_trade, 2)}</div></div>
        </div>
      )}
      {showForm && (
        <div className="panel p-4 mb-3">
          <div className="flex flex-wrap items-end gap-3">
            <button onClick={() => setSide("LONG")} className={`px-4 py-2 rounded-sm font-head font-bold ${side === "LONG" ? "border border-emerald-500/60 text-emerald-400" : "border border-[#1d2635] text-slate-400"}`}>LONG</button>
            <button onClick={() => setSide("SHORT")} className={`px-4 py-2 rounded-sm font-head font-bold ${side === "SHORT" ? "border border-rose-500/60 text-rose-400" : "border border-[#1d2635] text-slate-400"}`}>SHORT</button>
            <button onClick={submit} disabled={busy} className="px-5 py-2 rounded-sm font-head font-bold text-black bg-cyan-500">{busy ? "Opening…" : `OPEN ${side}`}</button>
          </div>
        </div>
      )}
      <div className="flex gap-1 mb-2">
        {[["open", `Open Positions (${openTrades.length})`], ["history", `History (${trades.length})`]].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} className={`font-mono-t text-[11px] px-3 py-1.5 rounded-sm ${tab === k ? "bg-cyan-500 text-black" : "bg-[#0d121b] text-slate-400 border border-[#1d2635]"}`}>{label}</button>
        ))}
      </div>
      <div className="panel">
        <TradeTable rows={tab === "open" ? openTrades : trades} onClose={doClose} testid={`paper-table-${tab}`} />
      </div>
    </div>
  );
};
