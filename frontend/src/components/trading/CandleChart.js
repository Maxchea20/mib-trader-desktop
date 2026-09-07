import React, { useEffect, useRef, useState } from "react";
import { createChart } from "lightweight-charts";

const DIR = {
  support: "#00f59b",
  resistance: "#ff3b56",
  fibonacci: "#a855f7",
  breakout: "#38bdf8",
  dynamic: "#eab308",
};

export const CandleChart = ({
  candles,
  levels = [],
  fvgZones = [],
  confluenceZones,
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

  const [bands, setBands] = useState([]);

  /*
   * ======================================================
   * CREATE CHART
   * ======================================================
   */
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(
      containerRef.current,
      {
        localization: {
          locale: "en-US",
        },

        layout: {
          background: {
            color: "#0d121b",
          },

          textColor: "#7d8ca3",

          fontFamily:
            "JetBrains Mono, monospace",

          fontSize: 10,
        },

        grid: {
          vertLines: {
            color:
              "rgba(45,58,80,0.25)",
          },

          horzLines: {
            color:
              "rgba(45,58,80,0.25)",
          },
        },

        rightPriceScale: {
          borderColor: "#1d2635",
        },

        timeScale: {
          borderColor: "#1d2635",
          timeVisible: true,
          secondsVisible: false,
        },

        crosshair: {
          mode: 0,

          vertLine: {
            color: "#38bdf8",
            width: 1,
            style: 3,
            labelBackgroundColor:
              "#0f6fb3",
          },

          horzLine: {
            color: "#38bdf8",
            width: 1,
            style: 3,
            labelBackgroundColor:
              "#0f6fb3",
          },
        },
      }
    );

    const candleSeries =
      chart.addCandlestickSeries({
        upColor: "#00f59b",
        downColor: "#ff3b56",

        borderUpColor:
          "#00f59b",

        borderDownColor:
          "#ff3b56",

        wickUpColor:
          "#00c88a",

        wickDownColor:
          "#e0304a",
      });

    const volSeries =
      chart.addHistogramSeries({
        priceFormat: {
          type: "volume",
        },

        priceScaleId: "vol",
      });

    chart
      .priceScale("vol")
      .applyOptions({
        scaleMargins: {
          top: 0.82,
          bottom: 0,
        },
      });

    chartRef.current = chart;

    candleSeriesRef.current =
      candleSeries;

    volSeriesRef.current =
      volSeries;

    const ro =
      new ResizeObserver(() => {
        if (!containerRef.current) {
          return;
        }

        chart.applyOptions({
          width:
            containerRef.current
              .clientWidth,

          height:
            containerRef.current
              .clientHeight,
        });
      });

    ro.observe(
      containerRef.current
    );

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
    if (
      !candleSeriesRef.current ||
      !candles?.length
    ) {
      return;
    }

    const cData =
      candles.map((c) => ({
        time: c.ts,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));

    const vData =
      candles.map((c) => ({
        time: c.ts,

        value:
          c.volume,

        color:
          c.close >= c.open
            ? "rgba(0,245,155,0.35)"
            : "rgba(255,59,86,0.35)",
      }));

    candleSeriesRef.current.setData(
      cData
    );

    volSeriesRef.current.setData(
      vData
    );

    lastCandleRef.current =
      cData.length
        ? {
            ...cData[
              cData.length - 1
            ],
          }
        : null;
  }, [candles]);

  /*
   * ======================================================
   * LIVE FORMING CANDLE
   * ======================================================
   */
  useEffect(() => {
    const series =
      candleSeriesRef.current;

    const last =
      lastCandleRef.current;

    if (
      !series ||
      !last ||
      livePrice == null
    ) {
      return;
    }

    const updated = {
      time: last.time,

      open: last.open,

      high: Math.max(
        last.high,
        livePrice
      ),

      low: Math.min(
        last.low,
        livePrice
      ),

      close: livePrice,
    };

    lastCandleRef.current =
      updated;

    try {
      series.update(updated);
    } catch (e) {}
  }, [livePrice]);

  /*
   * ======================================================
   * PRICE LEVELS
   * ======================================================
   */
  useEffect(() => {
    if (
      !candleSeriesRef.current
    ) {
      return;
    }

    priceLinesRef.current.forEach(
      (pl) => {
        try {
          candleSeriesRef.current.removePriceLine(
            pl
          );
        } catch (e) {}
      }
    );

    priceLinesRef.current = [];

    levels
      .slice(0, 10)
      .forEach((lv) => {
        const line =
          candleSeriesRef.current.createPriceLine(
            {
              price:
                lv.price,

              color:
                DIR[lv.type] ||
                "#64748b",

              lineWidth: 1,

              lineStyle: 2,

              axisLabelVisible:
                true,

              title:
                lv.label?.slice(
                  0,
                  16
                ) || "",
            }
          );

        priceLinesRef.current.push(
          line
        );
      });
  }, [levels]);

  /*
   * ======================================================
   * FVG OVERLAY
   *
   * IMPORTANT:
   * This renderer is FVG ONLY.
   *
   * Confluence is intentionally NOT rendered.
   *
   * FVG:
   *   X = actual creation candle
   *   Y = FVG price zone
   *   WIDTH = creation candle -> chart right edge
   *
   * The backend provides the FVG timestamp.
   * We map that timestamp to the actual candle
   * index in the same candle array used by the chart.
   * ======================================================
   */
  useEffect(() => {
    const compute = () => {
      const series =
        candleSeriesRef.current;

      const chart =
        chartRef.current;

      const cont =
        containerRef.current;

      if (
        !series ||
        !chart ||
        !cont ||
        !candles?.length
      ) {
        setBands([]);
        return;
      }

      const h =
        cont.clientHeight;

      const w =
        cont.clientWidth;

      const timeScale =
        chart.timeScale();

      const out = [];

      /*
       * --------------------------------------------------
       * RIGHT EDGE OF CANDLE PLOT
       * --------------------------------------------------
       */
      let priceScaleWidth =
        58;

      try {
        const actualWidth =
          chart
            .priceScale("right")
            .width();

        if (
          Number.isFinite(
            actualWidth
          ) &&
          actualWidth > 0
        ) {
          priceScaleWidth =
            actualWidth;
        }
      } catch (e) {}

      const plotRight =
        Math.max(
          0,
          w - priceScaleWidth
        );

      /*
       * --------------------------------------------------
       * TIMESTAMP -> ACTUAL CANDLE INDEX
       * --------------------------------------------------
       */
      const candleIndexByTime =
        new Map();

      candles.forEach(
        (c, index) => {
          candleIndexByTime.set(
            Number(c.ts),
            index
          );
        }
      );

      /*
       * --------------------------------------------------
       * GET FVG X POSITION
       * --------------------------------------------------
       *
       * Priority:
       *
       * 1. Actual candle index
       * 2. Lightweight Charts time index
       * 3. Direct time coordinate
       *
       * This prevents the FVG from disappearing
       * simply because timeToCoordinate() returns null.
       * --------------------------------------------------
       */
      const getFvgX =
        (timestamp) => {
          if (
            timestamp == null
          ) {
            return null;
          }

          const ts =
            Number(timestamp);

          /*
           * 1. Exact candle match.
           */
          const candleIndex =
            candleIndexByTime.get(
              ts
            );

          if (
            candleIndex !==
            undefined
          ) {
            try {
              const x =
                timeScale.logicalToCoordinate(
                  candleIndex
                );

              if (
                x != null &&
                Number.isFinite(x)
              ) {
                return x;
              }
            } catch (e) {}
          }

          /*
           * 2. Lightweight Charts
           * time -> logical index.
           */
          try {
            const logical =
              timeScale.timeToIndex(
                ts,
                true
              );

            if (
              logical != null
            ) {
              const x =
                timeScale.logicalToCoordinate(
                  logical
                );

              if (
                x != null &&
                Number.isFinite(x)
              ) {
                return x;
              }
            }
          } catch (e) {}

          /*
           * 3. Direct timestamp
           * conversion.
           */
          try {
            const x =
              timeScale.timeToCoordinate(
                ts
              );

            if (
              x != null &&
              Number.isFinite(x)
            ) {
              return x;
            }
          } catch (e) {}

          return null;
        };

      /*
       * --------------------------------------------------
       * RENDER FVGs
       * --------------------------------------------------
       */
      (fvgZones || []).forEach(
        (zone, index) => {
          if (
            zone?.low == null ||
            zone?.high == null ||
            zone?.timestamp ==
              null
          ) {
            return;
          }

          /*
           * Never render filled FVGs.
           */
          const state =
            zone.state ||
            (
              zone.mitigated
                ? "partial"
                : "fresh"
            );

          if (
            state === "filled"
          ) {
            return;
          }

          /*
           * Find actual creation
           * candle X position.
           */
          const xStart =
            getFvgX(
              zone.timestamp
            );

          if (
            xStart == null
          ) {
            return;
          }

          /*
           * If the FVG started before
           * the visible chart, clip it
           * to the left edge.
           */
          const left =
            Math.max(
              0,
              xStart
            );

          /*
           * If creation candle is
           * beyond the right edge,
           * nothing is visible.
           */
          if (
            left >= plotRight
          ) {
            return;
          }

          /*
           * ------------------------------------------------
           * PRICE -> SCREEN Y
           * ------------------------------------------------
           */
          let yTop =
            series.priceToCoordinate(
              Math.max(
                zone.low,
                zone.high
              )
            );

          let yBot =
            series.priceToCoordinate(
              Math.min(
                zone.low,
                zone.high
              )
            );

          if (
            yTop == null ||
            yBot == null
          ) {
            return;
          }

          /*
           * Clip to chart height.
           */
          yTop = Math.max(
            0,
            yTop
          );

          yBot = Math.min(
            h,
            yBot
          );

          if (
            yTop > h ||
            yBot < 0
          ) {
            return;
          }

          const height =
            Math.max(
              2,
              yBot - yTop
            );

          /*
           * Extend the FVG from
           * creation candle to the
           * current chart edge.
           */
          const width =
            Math.max(
              2,
              plotRight - left
            );

          const bullish =
            zone.type ===
            "fvg_bullish";

          /*
           * ------------------------------------------------
           * FVG VISUAL STYLE
           * ------------------------------------------------
           */
          let background;
          let border;

          if (bullish) {
            if (
              state ===
              "partial"
            ) {
              background =
                "rgba(16,185,129,0.08)";

              border =
                "rgba(16,185,129,0.30)";
            } else {
              background =
                "rgba(16,185,129,0.18)";

              border =
                "rgba(16,185,129,0.60)";
            }
          } else {
            if (
              state ===
              "partial"
            ) {
              background =
                "rgba(244,63,94,0.08)";

              border =
                "rgba(244,63,94,0.30)";
            } else {
              background =
                "rgba(244,63,94,0.18)";

              border =
                "rgba(244,63,94,0.60)";
            }
          }

          out.push({
            key:
              `fvg-${index}-${zone.timestamp}`,

            kind: "fvg",

            left,

            width,

            top: yTop,

            height,

            color:
              background,

            borderColor:
              border,

            label:
              `FVG ${
                bullish
                  ? "▲"
                  : "▼"
              } ${state}`,

            state,

            quality:
              zone.quality,
          });
        }
      );

      /*
       * IMPORTANT:
       *
       * No confluenceZones.forEach()
       * here.
       *
       * Confluence is NOT part of
       * the FVG visualization test.
       */

      setBands(out);
    };

    /*
     * Initial calculation.
     */
    compute();

    /*
     * Recalculate when the chart
     * is zoomed or scrolled.
     */
    const chart =
      chartRef.current;

    let unsubRange =
      () => {};

    if (chart) {
      const handler =
        () => {
          compute();
        };

      chart
        .timeScale()
        .subscribeVisibleLogicalRangeChange(
          handler
        );

      unsubRange =
        () => {
          chart
            .timeScale()
            .unsubscribeVisibleLogicalRangeChange(
              handler
            );
        };
    }

    /*
     * Recalculate when the window
     * changes size.
     */
    const resizeHandler =
      () => {
        compute();
      };

    window.addEventListener(
      "resize",
      resizeHandler
    );

    /*
     * Keep FVG synchronized with
     * live chart movement.
     */
    const iv =
      setInterval(
        compute,
        500
      );

    return () => {
      clearInterval(iv);

      unsubRange();

      window.removeEventListener(
        "resize",
        resizeHandler
      );
    };
  }, [
    fvgZones,
    candles,
  ]);

  /*
   * ======================================================
   * MARKET STRUCTURE MARKERS
   *
   * Uses backend MarketState.
   * Frontend does NOT detect pivots.
   * ======================================================
   */
  useEffect(() => {
    const series =
      candleSeriesRef.current;

    if (!series) {
      return;
    }

    const state =
      marketState;

    if (!state?.swings) {
      try {
        series.setMarkers([]);
      } catch (e) {}

      return;
    }

    const highs =
      state.swings.highs || [];

    const lows =
      state.swings.lows || [];

    const swings = [
      ...highs.map(
        (s) => ({
          ...s,
          kind: "HIGH",
        })
      ),

      ...lows.map(
        (s) => ({
          ...s,
          kind: "LOW",
        })
      ),
    ].sort(
      (a, b) =>
        a.timestamp -
        b.timestamp
    );

    const markers = [];

    let previousHigh =
      null;

    let previousLow =
      null;

    swings.forEach(
      (swing) => {
        let label = null;

        /*
         * HIGH
         */
        if (
          swing.kind ===
          "HIGH"
        ) {
          if (
            previousHigh !==
            null
          ) {
            if (
              swing.price >
              previousHigh
            ) {
              label = "HH";
            } else if (
              swing.price <
              previousHigh
            ) {
              label = "LH";
            }
          }

          previousHigh =
            swing.price;
        }

        /*
         * LOW
         */
        if (
          swing.kind ===
          "LOW"
        ) {
          if (
            previousLow !==
            null
          ) {
            if (
              swing.price >
              previousLow
            ) {
              label = "HL";
            } else if (
              swing.price <
              previousLow
            ) {
              label = "LL";
            }
          }

          previousLow =
            swing.price;
        }

        /*
         * First swing of each
         * type has no comparison.
         */
        if (!label) {
          return;
        }

        markers.push({
          time:
            swing.timestamp,

          position:
            swing.kind ===
            "HIGH"
              ? "aboveBar"
              : "belowBar",

          color:
            swing.kind ===
            "HIGH"
              ? "#ff3b56"
              : "#00f59b",

          shape:
            swing.kind ===
            "HIGH"
              ? "arrowDown"
              : "arrowUp",

          text: label,
        });
      }
    );

    /*
     * Native Lightweight Charts
     * markers stay attached to
     * their candles.
     */
    try {
      series.setMarkers(
        markers
      );
    } catch (e) {
      console.error(
        "Failed to set structure markers:",
        e
      );
    }
  }, [marketState]);

  /*
   * ======================================================
   * RENDER
   * ======================================================
   */
  return (
    <div
      className="relative w-full h-full overflow-hidden"
      data-testid="trading-chart-container"
    >
      <div
        ref={containerRef}
        className="w-full h-full"
      />

      {/*
        FVG overlays ONLY.
        No Confluence overlay.
      */}
      <div
        className="absolute inset-0 pointer-events-none overflow-hidden"
        data-testid="chart-overlays"
      >
        {bands.map((b) => (
          <div
            key={b.key}
            className="absolute"
            style={{
              left: `${b.left}px`,
              top: `${b.top}px`,
              width: `${b.width}px`,
              height: `${b.height}px`,
              background: b.color,
              borderTop:
                `1px solid ${b.borderColor}`,
              borderBottom:
                `1px solid ${b.borderColor}`,
              zIndex: 5,
            }}
          >
            <span
              className="absolute left-1 top-0 font-mono-t text-[8px] leading-none px-1 py-0.5"
              style={{
                color: b.borderColor,
              }}
            >
              {b.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};