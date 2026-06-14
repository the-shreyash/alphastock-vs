import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries, HistogramSeries } from "lightweight-charts";
import { useTheme } from "../../context/ThemeContext";

export default function TradingChart({ data, symbol, height = 360 }) {
  const chartRef = useRef(null);
  const containerRef = useRef(null);
  const { theme } = useTheme();

  useEffect(() => {
    if (!containerRef.current || !data?.length) return;

    const isDark = theme === "dark";
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { type: "solid", color: isDark ? "#18181B" : "#FFFFFF" },
        textColor: isDark ? "#A1A1AA" : "#71717A",
        fontFamily: "JetBrains Mono, monospace",
        fontSize: 11,
      },
      localization: {
        locale: "en-US",
      },
      grid: {
        vertLines: { color: isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)" },
        horzLines: { color: isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)" },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: isDark ? "#52525B" : "#D4D4D8", style: 2, width: 1 },
        horzLine: { color: isDark ? "#52525B" : "#D4D4D8", style: 2, width: 1 },
      },
      rightPriceScale: {
        borderColor: isDark ? "#27272A" : "#E4E4E7",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: isDark ? "#27272A" : "#E4E4E7",
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    });

    chartRef.current = chart;

    // Candlestick series (v5 API)
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#10B981",
      downColor: "#F43F5E",
      borderDownColor: "#F43F5E",
      borderUpColor: "#10B981",
      wickDownColor: "#F43F5E",
      wickUpColor: "#10B981",
    });

    // Format data for lightweight-charts - use unix timestamps
    const chartData = data.map((d, idx) => {
      let time;
      try {
        if (d.time && (d.time.includes("T") || d.time.includes("+"))) {
          time = Math.floor(new Date(d.time).getTime() / 1000);
        } else if (d.time && d.time.match(/^\d{4}-\d{2}-\d{2}$/)) {
          time = Math.floor(new Date(d.time + "T00:00:00Z").getTime() / 1000);
        } else {
          time = Math.floor(Date.now() / 1000) - (data.length - idx) * 300;
        }
      } catch {
        time = Math.floor(Date.now() / 1000) - (data.length - idx) * 300;
      }
      return { time, open: d.open, high: d.high, low: d.low, close: d.close };
    }).filter((d, i, arr) => i === 0 || d.time > arr[i - 1].time); // Ensure ascending order

    candleSeries.setData(chartData);

    // Volume series (v5 API)
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const volumeData = chartData.map((cd, i) => ({
      time: cd.time,
      value: data[i]?.volume || 0,
      color: data[i]?.close >= data[i]?.open ? "rgba(16,185,129,0.3)" : "rgba(244,63,94,0.3)",
    }));

    volumeSeries.setData(volumeData);

    chart.timeScale().fitContent();

    // Resize observer
    const observer = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [data, theme, height]);

  if (!data?.length) {
    return (
      <div className="flex items-center justify-center rounded-xl" style={{ height, background: "var(--bg-surface)" }}>
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>No chart data available</span>
      </div>
    );
  }

  return (
    <div data-testid={`trading-chart-${symbol || "default"}`} className="rounded-xl overflow-hidden border" style={{ borderColor: "var(--border)" }}>
      <div ref={containerRef} style={{ width: "100%", height }} />
    </div>
  );
}
