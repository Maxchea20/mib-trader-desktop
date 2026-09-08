import React, { useEffect, useRef } from "react";
import { createChart } from "lightweight-charts";
import { useFvgOverlay } from "./chart/useFvgOverlay";
import { useStructureMarkers } from "./chart/useStructureMarkers";
import { useStructureEventOverlay } from "./chart/useStructureEventOverlay";
import { FvgOverlayLayer } from "./chart/FvgOverlayLayer";
import { StructureEventOverlayLayer } from "./chart/StructureEventOverlayLayer";

const DIR = {
  support: "#00f59b",
  resistance: "#ff3b56",
  fibonacci: "#a855f7",
  breakout: "#38bdf8",
  dynamic: "#eab308",
};

// This file is intentionally kept lean: chart creation, candle/volume
// data, the live-forming candle, and price lines only. FVG overlay
// computation, FVG rendering, and Market Structure markers each moved
// into their own file under ./chart/ — see useFvgOverlay.js,
// FvgOverlayLayer.js, and useStructureMarkers.js. Nothing here changes
// behavior versus before; it's purely relocated so each concern can be
// read, tested, and extended independently instead of all living inside
// one dense file.
export const CandleChart = ({
  candles,
  levels = [],
  fvgZones = [],
  confluenceZones, // eslint-disable-line no-unused-vars -- intentionally
  // unused: Confluence is NOT part of the FVG/chart overlay by design,
  // kept as a named (ignored) prop so callers don't need to know that.
  livePrice = null,
  timeframe,
  marketState = null,
}) => {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volSeriesRef = useRef(null);
  const priceLinesRef = useRef([]);
  const lastCandleRef = useRef(null);

  /*
   * ======================================================
   * CREATE CHART
   * ======================================================
   */
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      localization: { locale: "en-US" },
      layout: {
        background: { color: "#0d121b" },
        textColor: "#7d8ca3",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(45,58,80,0.25)" },
        horzLines: { color: "rgba(45,58,80,0.25)" },
      },
      rightPriceScale: { borderColor: "#1d2635" },
      timeScale: { borderColor: "#1d2635", timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: 0,
        vertLine: { color: "#38bdf8", width: 1, style: 3, labelBackgroundColor: "#0f6fb3" },
        horzLine: { color: "#38bdf8", width: 1, style: 3, labelBackgroundColor: "#0f6fb3" },
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#00f59b",
      downColor: "#ff3b56",
      borderUpColor: "#00f59b",
      borderDownColor: "#ff3b56",
      wickUpColor: "#00c88a",
      wickDownColor: "#e0304a",
    });

    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });

    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volSeriesRef.current = volSeries;

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, []);

  /*
   * ======================================================
   * CANDLE DATA
   * ======================================================
   */
  useEffect(() => {
    if (!candleSeriesRef.current || !candles?.length) return;

    const cData = candles.map((c) => ({
      time: c.ts, open: c.open, high: c.high, low: c.low, close: c.close,
    }));
    const vData = candles.map((c) => ({
      time: c.ts,
      value: c.volume,
      color: c.close >= c.open ? "rgba(0,245,155,0.35)" : "rgba(255,59,86,0.35)",
    }));

    candleSeriesRef.current.setData(cData);
    volSeriesRef.current.setData(vData);
    lastCandleRef.current = cData.length ? { ...cData[cData.length - 1] } : null;
  }, [candles]);

  /*
   * ======================================================
   * LIVE FORMING CANDLE
   * ======================================================
   */
  useEffect(() => {
    const series = candleSeriesRef.current;
    const last = lastCandleRef.current;
    if (!series || !last || livePrice == null) return;

    const updated = {
      time: last.time,
      open: last.open,
      high: Math.max(last.high, livePrice),
      low: Math.min(last.low, livePrice),
      close: livePrice,
    };
    lastCandleRef.current = updated;
    try { series.update(updated); } catch (e) {}
  }, [livePrice]);

  /*
   * ======================================================
   * PRICE LEVELS
   * ======================================================
   */
  useEffect(() => {
    if (!candleSeriesRef.current) return;

    priceLinesRef.current.forEach((pl) => {
      try { candleSeriesRef.current.removePriceLine(pl); } catch (e) {}
    });
    priceLinesRef.current = [];

    levels.slice(0, 10).forEach((lv) => {
      const line = candleSeriesRef.current.createPriceLine({
        price: lv.price,
        color: DIR[lv.type] || "#64748b",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: lv.label?.slice(0, 16) || "",
      });
      priceLinesRef.current.push(line);
    });
  }, [levels]);

  /*
   * ======================================================
   * FVG OVERLAY (computation extracted to useFvgOverlay,
   * rendering extracted to FvgOverlayLayer)
   * ======================================================
   */
  const bands = useFvgOverlay({ chartRef, candleSeriesRef, containerRef, candles, fvgZones });

  /*
   * ======================================================
   * MARKET STRUCTURE MARKERS (extracted to useStructureMarkers)
   * ======================================================
   */
  useStructureMarkers({ candleSeriesRef, marketState });

  /*
   * ======================================================
   * BOS / CHoCH STRUCTURAL BREAK LINES
   *
   * Horizontal line at the actual broken price level, from the swing
   * point to the break candle — not a floating arrow. See
   * useStructureEventOverlay.js for why this replaced the earlier
   * marker-based approach.
   * ======================================================
   */
  const structureEventLines = useStructureEventOverlay({
    chartRef, candleSeriesRef, containerRef, candles,
    events: marketState?.structure?.events,
  });

  /*
   * ======================================================
   * RENDER
   * ======================================================
   */
  return (
    <div className="relative w-full h-full overflow-hidden" data-testid="trading-chart-container">
      <div ref={containerRef} className="w-full h-full" />
      <FvgOverlayLayer bands={bands} />
      <StructureEventOverlayLayer lines={structureEventLines} />
    </div>
  );
};