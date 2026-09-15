import React, { useEffect, useState, useCallback } from "react";
import { TrendingUp, TrendingDown, Wallet, Bot, Plus } from "lucide-react";
import {
  openPaperTrade, closePaperTrade, getPaperTrades, getPaperStats,
  getAutotrade, updateAutotrade, getMexcAccount,
} from "../../lib/api";
import { dirStyle, fmt } from "../../lib/style";

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");


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
        if (active) setAcct({ connected: false, error: "request failed" });
      }
      if (active) timeoutId = setTimeout(tick, 5000);
    };
    tick();
    return () => {
      active = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, []);

  const cfg = auto?.config || {};
  const sizing = auto?.sizing_preview || {};
  const sizingMode = cfg.sizing_mode || "NORMAL";
  const armed = !!auto?.live_armed;

  const [allocationInput, setAllocationInput] = useState(cfg.allocation_pct ?? 20);
  const [maxNotionalInput, setMaxNotionalInput] = useState(cfg.max_live_notional_usd ?? 1000);
  const [refreshing, setRefreshing] = useState(false);
  const [fieldWarnings, setFieldWarnings] = useState({});
  const [refreshFailed, setRefreshFailed] = useState(null);

  useEffect(() => { if (cfg.allocation_pct != null) setAllocationInput(cfg.allocation_pct); }, [cfg.allocation_pct]);
  useEffect(() => { if (cfg.max_live_notional_usd != null) setMaxNotionalInput(cfg.max_live_notional_usd); }, [cfg.max_live_notional_usd]);

  const connected = acct?.connected;
  const leverage = cfg.leverage ?? 10;
  const minLev = sizing.min_leverage;
  const maxLev = sizing.max_leverage;

  const saveField = async (field, value, setLocal) => {
    const a = await onSave({ [field]: value });
    const warn = (a?.warnings || []).find((w) => w.startsWith(field + ":"));
    setFieldWarnings((prev) => ({ ...prev, [field]: warn || null }));
    if (a?.config && a.config[field] != null) setLocal(a.config[field]);
  };

  const doRefresh = async () => {
    setRefreshing(true);
    setRefreshFailed(null);
    try {
      const a = await onSave({ refresh_normal_base: true });
      const warn = (a?.warnings || []).find((w) => w.startsWith("refresh_normal_base:"));
      if (warn) setRefreshFailed(warn);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="panel mb-3 p-3" data-testid="live-trading-controls">
      <div className="flex items-center justify-between mb-3">
        <div className="font-head font-bold text-slate-200 tracking-wide">
          LIVE AUTO-TRADE CONTROLS
        </div>
        <span className={`font-mono-t text-[10px] px-2 py-1 rounded-sm border ${
          armed
            ? "text-amber-400 border-amber-500/40 bg-amber-500/5"
            : "text-slate-400 border-[#1d2635] bg-[#0d121b]"
        }`}>
          {armed ? "REAL ACCOUNT · ARMED" : "REAL ACCOUNT · NOT ARMED"}
        </span>
      </div>

      {!armed && (
        <div className="mb-3 px-2 py-1.5 border border-amber-500/40 bg-amber-500/5 font-mono-t text-[11px] text-amber-300" data-testid="live-not-armed">
          Hunt is watching. No real order until backend/.env has MEXC_LIVE_TRADING_ENABLED=true and you restart.
        </div>
      )}

      {acct && !connected && (
        <div className="mb-3 px-2 py-1.5 border border-rose-500/40 bg-rose-500/5 font-mono-t text-[11px] text-rose-300" data-testid="mexc-account-error">
          MEXC account not connected: {acct.error || "unknown error"}
        </div>
      )}

      {sizing.error && (
        <div className="mb-3 px-2 py-1.5 border border-rose-500/40 bg-rose-500/5 font-mono-t text-[11px] text-rose-300" data-testid="sizing-error">
          Sizing not currently valid: {sizing.error}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">ACCOUNT BALANCE</div>
          <div className="font-mono-t text-lg text-slate-100">
            {connected ? `${fmt(acct.equity, 2)} USDT` : "? USDT"}
          </div>
        </div>

        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">AVAILABLE</div>
          <div className="font-mono-t text-lg text-slate-100">
            {connected ? `${fmt(acct.available_balance, 2)} USDT` : "? USDT"}
          </div>
        </div>

        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">UNREALIZED PNL</div>
          <div className="font-mono-t text-lg">
            {connected ? <Pnl v={acct.unrealized_pnl} /> : <span className="text-slate-400">?</span>}
          </div>
        </div>

        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">MARGIN</div>
          <div className="font-mono-t text-lg text-slate-100">Isolated</div>
        </div>
      </div>

      <div className="font-head font-bold text-slate-200 tracking-wide text-sm mb-2">
        POSITION SIZING
      </div>

      <div className="flex gap-1 mb-3" data-testid="sizing-mode-tabs">
        {["NORMAL", "COMPOUNDING"].map((m) => (
          <button
            key={m}
            onClick={() => onSave({ sizing_mode: m })}
            data-testid={`sizing-mode-${m.toLowerCase()}`}
            className={`px-3 py-1.5 border font-mono-t text-[11px] rounded-sm transition-colors ${
              sizingMode === m
                ? "border-amber-500/60 bg-amber-500/10 text-amber-300"
                : "border-[#1d2635] bg-[#0d121b] text-slate-400"
            }`}
          >
            {m === "NORMAL" ? "NORMAL TRADE" : "COMPOUNDING"}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        {sizingMode === "NORMAL" ? (
          <div>
            <div className="widget-label mb-1">NORMAL BASE</div>
            <div className="flex items-center gap-2">
              <div className="flex-1 px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] font-mono-t text-xs text-slate-300 rounded-sm" data-testid="normal-base-value">
                {sizing.normal_base != null ? `$${fmt(sizing.normal_base, 2)}` : "—"}
              </div>
              <button
                onClick={doRefresh}
                disabled={refreshing}
                data-testid="refresh-normal-base"
                className="px-2 py-1.5 border border-[#1d2635] bg-[#0d121b] text-slate-300 hover:border-amber-500/60 hover:text-amber-300 font-mono-t text-[11px] rounded-sm disabled:opacity-50"
              >
                {refreshing ? "…" : "Refresh"}
              </button>
            </div>
            {refreshFailed && (
              <div className="mt-1 font-mono-t text-[10px] text-rose-300" data-testid="refresh-failed-warning">
                Refresh failed — {refreshFailed.replace(/^refresh_normal_base:\s*/, "")}
              </div>
            )}
          </div>
        ) : (
          <div>
            <div className="widget-label mb-1">ACCOUNT BALANCE (LIVE)</div>
            <div className="px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] font-mono-t text-xs text-slate-300 rounded-sm">
              {sizing.balance_used != null ? `$${fmt(sizing.balance_used, 2)}` : "?"}
            </div>
          </div>
        )}

        <div>
          <div className="widget-label mb-1">ALLOCATION</div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min="0.1"
              max="100"
              step="0.1"
              value={allocationInput}
              onChange={(e) => setAllocationInput(e.target.value)}
              onBlur={() => saveField("allocation_pct", Number(allocationInput), setAllocationInput)}
              data-testid="allocation-input"
              className="w-full px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] text-slate-200 font-mono-t text-xs rounded-sm"
            />
            <span className="font-mono-t text-xs text-slate-400">%</span>
          </div>
          {fieldWarnings.allocation_pct && (
            <div className="mt-1 font-mono-t text-[10px] text-rose-300" data-testid="allocation-warning">
              {fieldWarnings.allocation_pct}
            </div>
          )}
        </div>

        <div>
          <div className="widget-label mb-1">
            LEVERAGE{minLev != null && maxLev != null ? ` (max ${fmt(maxLev, 0)}x for this contract)` : ""}
          </div>
          <div className="flex gap-1">
            {[10, 20, 50, 100].map((x) => {
              const outOfBounds = (minLev != null && x < minLev) || (maxLev != null && x > maxLev);
              return (
                <button
                  key={x}
                  onClick={() => !outOfBounds && saveField("leverage", x, () => {})}
                  disabled={outOfBounds}
                  title={outOfBounds ? `Exceeds this contract's allowed leverage range` : undefined}
                  data-testid={`leverage-${x}`}
                  className={`flex-1 px-2 py-1.5 border font-mono-t text-[10px] rounded-sm transition-colors ${
                    outOfBounds
                      ? "border-[#1d2635] bg-[#0d121b] text-slate-600 cursor-not-allowed opacity-50"
                      : Number(leverage) === x
                      ? "border-amber-500/60 bg-amber-500/10 text-amber-300"
                      : "border-[#1d2635] bg-[#0d121b] text-slate-400"
                  }`}
                >
                  {x}x
                </button>
              );
            })}
          </div>
          {fieldWarnings.leverage && (
            <div className="mt-1 font-mono-t text-[10px] text-rose-300" data-testid="leverage-warning">
              {fieldWarnings.leverage}
            </div>
          )}
        </div>

        <div>
          <div className="widget-label mb-1">MAX NOTIONAL (SAFETY CAP)</div>
          <input
            type="number"
            min="1"
            value={maxNotionalInput}
            onChange={(e) => setMaxNotionalInput(e.target.value)}
            onBlur={() => saveField("max_live_notional_usd", Number(maxNotionalInput), setMaxNotionalInput)}
            data-testid="max-notional-input"
            className="w-full px-2 py-1.5 bg-[#0d121b] border border-[#1d2635] text-slate-200 font-mono-t text-xs rounded-sm"
          />
          {fieldWarnings.max_live_notional_usd && (
            <div className="mt-1 font-mono-t text-[10px] text-rose-300" data-testid="max-notional-warning">
              {fieldWarnings.max_live_notional_usd}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3" data-testid="sizing-preview">
        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">CALCULATED CAPITAL</div>
          <div className="font-mono-t text-base text-slate-100">
            {sizing.calculated_capital != null ? `$${fmt(sizing.calculated_capital, 2)}` : "—"}
          </div>
        </div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">CALCULATED NOTIONAL</div>
          <div className="font-mono-t text-base text-slate-100">
            {sizing.calculated_notional != null ? `$${fmt(sizing.calculated_notional, 2)}` : "—"}
          </div>
        </div>
        <div className={`border p-2 ${sizing.capped ? "border-amber-500/50 bg-amber-500/5" : "border-[#1d2635] bg-[#0d121b]"}`}>
          <div className="widget-label">FINAL NOTIONAL — CAPPED: {sizing.capped ? "YES" : "NO"}</div>
          <div className={`font-mono-t text-base ${sizing.capped ? "text-amber-300" : "text-slate-100"}`}>
            {sizing.final_notional != null ? `$${fmt(sizing.final_notional, 2)}` : "—"}
          </div>
        </div>
        <div className="border border-[#1d2635] bg-[#0d121b] p-2">
          <div className="widget-label">CONTRACT QUANTITY</div>
          <div className="font-mono-t text-base text-slate-100">
            {sizing.final_quantity != null ? fmt(sizing.final_quantity, 0) : "—"}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mt-1">
        <div>
          <div className="widget-label mb-1">STOP LOSS (HUNT · PNL)</div>
          <div className="px-2 py-1.5 bg-[#0d121b] border border-rose-500/30 font-mono-t text-xs text-rose-300 rounded-sm" data-testid="hunt-sl-pnl">
            {auto?.sl_pnl_usd != null ? `${auto.sl_pnl_usd >= 0 ? "+" : ""}${fmt(auto.sl_pnl_usd, 2)} USDT` : "waiting for Hunt levels"}
            {auto?.sl_price != null ? `  @ ${fmt(auto.sl_price, 1)}` : ""}
          </div>
        </div>

        <div>
          <div className="widget-label mb-1">TAKE PROFIT (HUNT · PNL)</div>
          <div className="px-2 py-1.5 bg-[#0d121b] border border-emerald-500/30 font-mono-t text-xs text-emerald-300 rounded-sm" data-testid="hunt-tp-pnl">
            {auto?.tp_pnl_usd != null ? `${auto.tp_pnl_usd >= 0 ? "+" : ""}${fmt(auto.tp_pnl_usd, 2)} USDT` : "waiting for Hunt levels"}
            {auto?.tp_price != null ? `  @ ${fmt(auto.tp_price, 1)}` : ""}
          </div>
        </div>

        <div>
          <div className="widget-label mb-1">EXECUTION</div>
          <div className="px-2 py-1.5 bg-[#0d121b] border border-amber-500/30 font-mono-t text-xs text-amber-400 rounded-sm">
            HUNT C-FI · ISOLATED {fmt(leverage, 0)}x
          </div>
        </div>
      </div>

      <div className="mt-3 font-mono-t text-[10px] text-slate-500">
        Allocation % is margin, not risk. 20% at 10x on a 500 account ≈ 1000 USDT notional (capped).
        SL/TP dollar PNL is computed from Hunt 1.5 / 2.5 ATR prices — you do not type them.
        Needs MEXC_API_KEY, MEXC_API_SECRET and MEXC_LIVE_TRADING_ENABLED=true on the backend.
      </div>
    </div>
  );
};

const Pnl = ({ v, pct }) => {
  if (v === null || v === undefined) return <span className="text-slate-500">—</span>;
  const c = v >= 0 ? "text-emerald-400" : "text-rose-400";
  return <span className={`font-mono-t ${c}`}>{v >= 0 ? "+" : ""}${fmt(v, 2)} {pct != null && <span className="text-[10px] opacity-70">({v >= 0 ? "+" : ""}{fmt(pct, 2)}%)</span>}</span>;
};

const TradeTable = ({ rows, onClose, testid }) => (
  <div className="overflow-x-auto" data-testid={testid}>
    <table className="w-full text-left">
      <thead>
        <tr className="widget-label border-b border-[#1d2635]">
          <th className="px-3 py-2">Src</th>
          <th className="px-3 py-2">Side</th>
          <th className="px-3 py-2">Status</th>
          <th className="px-3 py-2">Entry</th>
          <th className="px-3 py-2">SL</th>
          <th className="px-3 py-2">TP</th>
          <th className="px-3 py-2">Exit</th>
          <th className="px-3 py-2">PnL</th>
          <th className="px-3 py-2">Opened</th>
          <th className="px-3 py-2"></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => {
          const st = dirStyle(t.side);
          const isOpen = t.status === "OPEN";
          return (
            <tr key={t.id} className="border-b border-[#161f2e] font-mono-t text-[11px]" data-testid={`paper-row-${t.id}`}>
              <td className="px-3 py-2">
                <span className={`text-[9px] px-1 py-0.5 rounded-sm border ${t.source === "AUTO" ? "border-cyan-500/50 text-cyan-400" : "border-slate-600 text-slate-400"}`}>
                  {t.source || "MANUAL"}
                </span>
              </td>
              <td className={`px-3 py-2 font-bold ${st.text}`}>{t.side}</td>
              <td className="px-3 py-2">
                <span className={isOpen ? "text-cyan-400" : "text-slate-400"}>{t.status}</span>
                {!isOpen && t.exit_reason && <span className="text-slate-600 ml-1">·{t.exit_reason}</span>}
              </td>
              <td className="px-3 py-2 text-slate-200">${fmt(t.entry_price, 1)}</td>
              <td className="px-3 py-2 text-rose-400/80">${fmt(t.sl_price, 1)}</td>
              <td className="px-3 py-2 text-emerald-400/80">${fmt(t.tp_price, 1)}</td>
              <td className="px-3 py-2 text-slate-300">{t.exit_price ? `$${fmt(t.exit_price, 1)}` : (isOpen ? `~$${fmt(t.mark_price, 1)}` : "—")}</td>
              <td className="px-3 py-2">{isOpen ? <Pnl v={t.unrealized_pnl} pct={t.unrealized_pnl_pct} /> : <Pnl v={t.pnl} pct={t.pnl_pct} />}</td>
              <td className="px-3 py-2 text-slate-500">{fmtTime(t.opened_at)}</td>
              <td className="px-3 py-2">
                {isOpen && (
                  <button onClick={() => onClose(t.id)} data-testid={`paper-close-${t.id}`}
                    className="text-[10px] px-2 py-0.5 rounded-sm border border-slate-600 text-slate-300 hover:border-rose-500 hover:text-rose-400">
                    Close
                  </button>
                )}
              </td>
            </tr>
          );
        })}
        {rows.length === 0 && (
          <tr><td colSpan="10" className="px-3 py-6 text-center font-mono-t text-[11px] text-slate-600">No trades yet</td></tr>
        )}
      </tbody>
    </table>
  </div>
);

export const PaperTradingPanel = ({ brain, livePrice, timeframe }) => {
  const [trades, setTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [markPrice, setMarkPrice] = useState(null);
  const [auto, setAuto] = useState(null);
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
      const [d, s, a] = await Promise.all([getPaperTrades(), getPaperStats(), getAutotrade()]);
      setTrades(d.trades || []);
      setMarkPrice(d.live_price);
      setStats(s);
      setAuto(a);
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
    return () => {
      active = false;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [refresh]);

  const toggleAuto = async () => {
    const next = !(auto?.config?.enabled);
    setAuto((p) => ({ ...(p || {}), config: { ...(p?.config || {}), enabled: next } }));
    try { const a = await updateAutotrade({ enabled: next }); setAuto(a); refresh(); } catch (e) {}
  };

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

  const toggleMode = async () => {
    const next = mode === "PAPER" ? "LIVE" : "PAPER";
    setAuto((p) => ({ ...(p || {}), config: { ...(p?.config || {}), mode: next } }));
    try {
      const a = await updateAutotrade({ mode: next });
      setAuto(a);
      refresh();
    } catch (e) {}
  };

  return (
    <div className="mt-4" data-testid="paper-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-2 px-0.5">
        <div className="flex items-center gap-2">
          <Wallet className="w-4 h-4 text-emerald-400" />
          <span className="font-head font-bold text-slate-200 tracking-wide text-lg">{mode === "LIVE" ? "LIVE TRADING" : "PAPER TRADING"}</span>
          <span className="widget-label">Persistent · Isolated · Auto SL/TP</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={toggleAuto}
            data-testid="autotrade-toggle"
            className={`flex items-center gap-2 px-3 py-1.5 rounded-sm border font-mono-t text-[11px] transition-colors ${
              autoOn ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-300" : "border-[#1d2635] bg-[#0d121b] text-slate-400"
            }`}
          >
            <Bot className={`w-3.5 h-3.5 ${autoOn ? "text-cyan-400" : "text-slate-500"}`} />
            <span>AUTO-TRADE</span>
            <span className={`w-8 h-4 rounded-full relative transition-colors ${autoOn ? "bg-cyan-500" : "bg-slate-700"}`}>
              <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-black transition-all ${autoOn ? "left-4" : "left-0.5"}`} />
            </span>
            <span className={autoOn ? "text-emerald-400" : "text-slate-500"}>{autoOn ? "ON" : "OFF"}</span>
          </button>

          <button
            onClick={toggleMode}
            data-testid="trading-mode-toggle"
            className={`flex items-center gap-2 px-3 py-1.5 rounded-sm border font-mono-t text-[11px] transition-colors ${
              mode === "PAPER"
                ? "border-emerald-500/60 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/60 bg-amber-500/10 text-amber-300"
            }`}
          >
            <span className="text-slate-400">MODE</span>
            <span>{mode}</span>
          </button>

          <button
            onClick={() => setShowForm((v) => !v)}
            data-testid="paper-new-toggle"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm border border-[#1d2635] bg-[#0d121b] text-slate-300 hover:border-cyan-500/60 font-mono-t text-[11px]"
          >
            <Plus className="w-3.5 h-3.5" /> New Trade
          </button>
        </div>
      </div>

      {auto && (
        <div className="font-mono-t text-[10px] text-slate-500 mb-2 px-0.5" data-testid="autotrade-status">
          Engine: <span className={autoOn ? "text-cyan-400" : "text-slate-500"}>{autoOn ? "running" : "paused"}</span>
          {" · "}mode <span className={mode === "LIVE" ? "text-amber-300" : "text-slate-300"}>{mode}</span>
          {mode === "LIVE" && (
            <>
              {" · "}armed <span className={auto.live_armed ? "text-amber-300" : "text-rose-300"}>{auto.live_armed ? "yes" : "no"}</span>
            </>
          )}
          {" · "}TF <span className="text-slate-300">{auto.config?.timeframe}</span>
          {" · "}last: <span className="text-slate-300">{auto.state?.last_action || "—"}</span>
          {auto.state?.last_reason ? ` (${auto.state.last_reason})` : ""}
        </div>
      )}

      {mode === "LIVE" && (
        <LiveTradingControls
          auto={auto}
          onSave={async (payload) => {
            try {
              const a = await updateAutotrade(payload);
              setAuto(a);
              return a;
            } catch (e) {
              return null;
            }
          }}
        />
      )}

      {stats && (
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-3" data-testid="paper-stats">
          <div className="panel p-2"><div className="widget-label">Trades</div><div className="font-mono-t text-lg text-slate-100">{stats.total_trades}</div></div>
          <div className="panel p-2"><div className="widget-label">Open</div><div className="font-mono-t text-lg text-cyan-400">{stats.open_trades}</div></div>
          <div className="panel p-2"><div className="widget-label">Win rate</div><div className="font-mono-t text-lg" style={{ color: stats.win_rate_pct >= 50 ? "#00f59b" : "#ffb800" }}>{fmt(stats.win_rate_pct, 1)}%</div></div>
          <div className="panel p-2"><div className="widget-label">Realized PnL</div><div className="font-mono-t text-lg" style={{ color: stats.total_realized_pnl >= 0 ? "#00f59b" : "#ff3b56" }} data-testid="paper-total-pnl">{stats.total_realized_pnl >= 0 ? "+" : ""}${fmt(stats.total_realized_pnl, 2)}</div></div>
          <div className="panel p-2"><div className="widget-label">Best</div><div className="font-mono-t text-lg text-emerald-400">+${fmt(stats.best_trade, 2)}</div></div>
          <div className="panel p-2"><div className="widget-label">Worst</div><div className="font-mono-t text-lg text-rose-400">${fmt(stats.worst_trade, 2)}</div></div>
        </div>
      )}

      {showForm && (
        <div className="panel p-4 mb-3" data-testid="paper-new-form">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex gap-2">
              <button onClick={() => setSide("LONG")} data-testid="paper-side-long"
                className={`flex items-center gap-1.5 px-4 py-2 rounded-sm font-head font-bold ${side === "LONG" ? "bg-emerald-950/60 border border-emerald-500/60 text-emerald-400" : "bg-[#0d121b] border border-[#1d2635] text-slate-400"}`}>
                <TrendingUp className="w-4 h-4" /> LONG
              </button>
              <button onClick={() => setSide("SHORT")} data-testid="paper-side-short"
                className={`flex items-center gap-1.5 px-4 py-2 rounded-sm font-head font-bold ${side === "SHORT" ? "bg-rose-950/60 border border-rose-500/60 text-rose-400" : "bg-[#0d121b] border border-[#1d2635] text-slate-400"}`}>
                <TrendingDown className="w-4 h-4" /> SHORT
              </button>
            </div>
            <label className="flex flex-col gap-1">
              <span className="widget-label">Entry (live)</span>
              <div className="font-mono-t text-sm text-white bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1.5 w-28">${fmt(entry, 1)}</div>
            </label>
            <label className="flex flex-col gap-1">
              <span className="widget-label">Size $</span>
              <input type="number" value={notional} onChange={(e) => setNotional(e.target.value)} data-testid="paper-notional"
                className="font-mono-t text-sm text-white bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1.5 w-24" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="widget-label">SL %</span>
              <input type="number" step="0.1" value={slPct} onChange={(e) => setSlPct(+e.target.value)} data-testid="paper-sl-pct"
                className="font-mono-t text-sm text-white bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1.5 w-20" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="widget-label">TP %</span>
              <input type="number" step="0.1" value={tpPct} onChange={(e) => setTpPct(+e.target.value)} data-testid="paper-tp-pct"
                className="font-mono-t text-sm text-white bg-[#0d121b] border border-[#1d2635] rounded-sm px-2 py-1.5 w-20" />
            </label>
            <div className="font-mono-t text-[11px] flex flex-col gap-0.5">
              <span className="text-rose-400">SL ${fmt(slPrice, 1)}</span>
              <span className="text-emerald-400">TP ${fmt(tpPrice, 1)}</span>
            </div>
            <button onClick={submit} disabled={busy} data-testid="paper-submit"
              className="px-5 py-2 rounded-sm font-head font-bold text-black bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50">
              {busy ? "Opening…" : `OPEN ${side}`}
            </button>
          </div>
        </div>
      )}

      <div className="flex gap-1 mb-2">
        {[["open", `Open Positions (${openTrades.length})`], ["history", `History (${trades.length})`]].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} data-testid={`paper-tab-${k}`}
            className={`font-mono-t text-[11px] px-3 py-1.5 rounded-sm transition-colors ${tab === k ? "bg-cyan-500 text-black" : "bg-[#0d121b] text-slate-400 border border-[#1d2635] hover:text-white"}`}>
            {label}
          </button>
        ))}
      </div>

      <div className="panel">
        <TradeTable rows={tab === "open" ? openTrades : trades} onClose={doClose} testid={`paper-table-${tab}`} />
      </div>
    </div>
  );
};
