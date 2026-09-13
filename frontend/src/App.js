import { useEffect, useState, useCallback, useRef } from "react";
import "@/index.css";
import { RefreshCw } from "lucide-react";
import {
  getTicker,
  getCandles,
  getAnalysis,
  getSyncStatus,
} from "@/lib/api";
import { AppHeader } from "@/components/trading/AppHeader";
import { CandleChart } from "@/components/trading/CandleChart";
import { BrainHeroPanel } from "@/components/trading/BrainHeroPanel";
import { ExplainabilityPanel } from "@/components/trading/ExplainabilityPanel";
import { ObservationLayer } from "@/components/trading/ObservationLayer";
import { MultiTimeframeRegime } from "@/components/trading/MultiTimeframeRegime";
import { KeyLevelsPanel } from "@/components/trading/KeyLevelsPanel";
import { SettingsPanel } from "@/components/trading/SettingsPanel";
import { BacktestHuntModal } from "@/components/trading/BacktestHuntModal";
import { PaperTradingPanel } from "@/components/trading/PaperTradingPanel";

function App() {
  const [timeframe, setTimeframe] = useState("15m");
  const [ticker, setTicker] = useState(null);
  const [live, setLive] = useState(null);
  const [candles, setCandles] = useState([]);
  const [analysis, setAnalysis] = useState(null);

  const [structureVisible, setStructureVisible] = useState(true);
  const [fvgVisible, setFvgVisible] = useState(true);

  const [syncStatus, setSyncStatus] = useState(null);
  const [hoveredType, setHoveredType] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [backtestOpen, setBacktestOpen] = useState(false);

  const tfRef = useRef(timeframe);
  tfRef.current = timeframe;

  const [livePrice, setLivePrice] = useState(null);
  const wsRef = useRef(null);

  const loadTicker = useCallback(async () => {
    try {
      const d = await getTicker();
      setLive(d);
      setTicker(d.ticker);
    } catch (e) {}
  }, []);

  const loadCandles = useCallback(async (tf) => {
    try {
      const d = await getCandles(tf, 500);
      if (tfRef.current === tf) setCandles(d.candles || []);
    } catch (e) {}
  }, []);

  const loadAnalysis = useCallback(async (tf) => {
    try {
      const d = await getAnalysis(tf);
      if (tfRef.current === tf) setAnalysis(d);
    } catch (e) {}
  }, []);

  const loadSync = useCallback(async () => {
    try { setSyncStatus(await getSyncStatus()); } catch (e) {}
  }, []);

  const refreshAll = useCallback(async (tf) => {
    setRefreshing(true);
    await Promise.all([loadCandles(tf), loadAnalysis(tf)]);
    setRefreshing(false);
  }, [loadCandles, loadAnalysis]);

  useEffect(() => {
    setCandles([]);
    setAnalysis(null);
    refreshAll(timeframe);
  }, [timeframe, refreshAll]);

  const startPoll = useCallback((fn, intervalMs, immediate, activeRef) => {
    let timeoutId = null;
    const tick = async () => {
      if (!activeRef.current) return;
      try { await fn(); } catch (e) {}
      if (activeRef.current) timeoutId = setTimeout(tick, intervalMs);
    };
    if (immediate) tick();
    else timeoutId = setTimeout(tick, intervalMs);
    return () => { if (timeoutId) clearTimeout(timeoutId); };
  }, []);

  useEffect(() => {
    const activeRef = { current: true };
    const stopTicker = startPoll(loadTicker, 20000, true, activeRef);
    const stopCandles = startPoll(() => loadCandles(tfRef.current), 8000, false, activeRef);
    const stopAnalysis = startPoll(() => loadAnalysis(tfRef.current), 12000, false, activeRef);
    const stopSync = startPoll(loadSync, 15000, true, activeRef);
    return () => {
      activeRef.current = false;
      [stopTicker, stopCandles, stopAnalysis, stopSync].forEach((stop) => stop());
    };
  }, [loadTicker, loadCandles, loadAnalysis, loadSync, startPoll]);

  useEffect(() => {
    let closed = false;
    let reconnectTimer = null;
    let currentWs = null;
    let reconnectDelay = 5000;
    const backendUrl = process.env.REACT_APP_BACKEND_URL || "http://127.0.0.1:8811";
    const url = `${backendUrl.replace(/^http/, "ws")}/api/ws/live`;
    const connect = () => {
      if (closed) return;
      if (currentWs && (currentWs.readyState === WebSocket.CONNECTING || currentWs.readyState === WebSocket.OPEN)) return;
      let ws;
      try {
        ws = new WebSocket(url);
        currentWs = ws;
        wsRef.current = ws;
      } catch (e) { scheduleReconnect(); return; }
      ws.onopen = () => { if (!closed) reconnectDelay = 5000; };
      ws.onmessage = (evt) => {
        try {
          const d = JSON.parse(evt.data);
          if (d.type === "tick") {
            if (d.price != null) setLivePrice(d.price);
            setLive((prev) => ({
              ...(prev || {}), connected: true, ws_connected: d.ws_connected,
              source: d.source, last_price: d.price, ticker: d.ticker || prev?.ticker,
            }));
            if (d.ticker && Object.keys(d.ticker).length > 2) setTicker(d.ticker);
          }
        } catch (e) {}
      };
      ws.onerror = () => {};
      ws.onclose = () => {
        if (currentWs === ws) { currentWs = null; wsRef.current = null; }
        if (!closed) scheduleReconnect();
      };
    };
    const scheduleReconnect = () => {
      if (closed || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!closed) connect();
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      }, reconnectDelay);
    };
    connect();
    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      const ws = currentWs;
      currentWs = null;
      wsRef.current = null;
      if (ws) try { ws.close(); } catch (e) {}
    };
  }, []);

  const levels = [];
  if (analysis?.agents) {
    const pull = (id) => analysis.agents.find((a) => a.agent === id)?.key_levels || [];
    pull("support_resistance").forEach((l) => levels.push(l));
    pull("fibonacci").slice(0, 4).forEach((l) => levels.push(l));
    pull("breakout").forEach((l) => levels.push(l));
    pull("trend").forEach((l) => levels.push(l));
  }
  const fvgZones = analysis?.agents?.find((a) => a.agent === "fair_value_gap")?.key_levels || [];
  const confluenceZones = analysis?.brain?.confluence_zones || [];

  return (
    <div className="min-h-screen scanlines" style={{ background: "#080b10" }}>
      <AppHeader
        ticker={ticker} live={live} timeframe={timeframe} onTimeframe={setTimeframe}
        syncStatus={syncStatus}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenBacktest={() => setBacktestOpen(true)}
      />
      <main className="pt-16 px-3 pb-6 max-w-[1800px] mx-auto">
        <div className="grid grid-cols-12 gap-3">
          <section className="col-span-12 xl:col-span-8 flex flex-col gap-3">
            <div className="flex items-center justify-between px-0.5">
              <div className="flex items-center gap-2">
                <span className="font-head font-bold text-slate-200 tracking-wide text-lg">MARKET DATA</span>
                <span className="widget-label">Layer 1 \u00b7 MEXC Futures \u00b7 SQLite</span>
              </div>
              <button data-testid="refresh-analysis-button" onClick={() => refreshAll(timeframe)}
                className="flex items-center gap-1.5 font-mono-t text-[11px] text-slate-300 px-2.5 py-1 rounded-sm bg-[#0d121b] border border-[#1d2635]">
                <RefreshCw className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} /> Recompute
              </button>
            </div>
            <div className="panel h-[420px] relative">
              <div className="absolute top-2 left-2 z-20 flex items-center gap-2">
                <button type="button" onClick={() => setStructureVisible((v) => !v)}
                  className={`flex items-center gap-1.5 px-2 py-1 rounded-sm border font-mono-t text-[10px] ${structureVisible ? "bg-cyan-500/10 border-cyan-500/50 text-cyan-300" : "bg-[#0d121b]/90 border-[#1d2635] text-slate-500"}`}>
                  STRUCTURE {structureVisible ? "ON" : "OFF"}
                </button>
                <button type="button" onClick={() => setFvgVisible((v) => !v)}
                  className={`flex items-center gap-1.5 px-2 py-1 rounded-sm border font-mono-t text-[10px] ${fvgVisible ? "bg-emerald-500/10 border-emerald-500/50 text-emerald-300" : "bg-[#0d121b]/90 border-[#1d2635] text-slate-500"}`}>
                  FVG {fvgVisible ? "ON" : "OFF"}
                </button>
              </div>
              {candles.length > 0 ? (
                <CandleChart candles={candles} levels={levels} fvgZones={fvgVisible ? fvgZones : []} confluenceZones={confluenceZones} livePrice={livePrice} timeframe={timeframe} marketState={structureVisible ? analysis?.market_state : null} />
              ) : (
                <div className="w-full h-full flex items-center justify-center widget-label">Loading local candles\u2026</div>
              )}
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
              <MultiTimeframeRegime htf={analysis?.htf_regime} />
              <div className="lg:col-span-2"><KeyLevelsPanel analysis={analysis} onHover={setHoveredType} /></div>
            </div>
          </section>
          <section className="col-span-12 xl:col-span-4 flex flex-col gap-3">
            <BrainHeroPanel brain={analysis?.brain} />
            <ExplainabilityPanel brain={analysis?.brain} />
          </section>
        </div>
        <div className="mt-4"><ObservationLayer agents={analysis?.agents || []} price={analysis?.price} /></div>
        <PaperTradingPanel brain={analysis?.brain} livePrice={livePrice} timeframe={timeframe} />
      </main>
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} onChanged={() => loadAnalysis(tfRef.current)} />
      <BacktestHuntModal open={backtestOpen} onClose={() => setBacktestOpen(false)} timeframe={timeframe} />
    </div>
  );
}

export default App;
