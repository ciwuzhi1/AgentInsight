"use client";

import { useEffect, useRef, useState } from "react";
// ECharts 按需引入：只注册 bar/line + 必要组件，减少 bundle ~70%
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DatasetComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// 注册 ECharts 组件（模块级一次即可，重复 use 是幂等的）
echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DatasetComponent,
  CanvasRenderer,
]);

/* 与后端 final.chart 对齐（CONTRACTS.md §6） */
export type ChartSpec = {
  type: string;
  x_field: string;
  y_fields: string[];
};

export type ChartBoxProps = {
  chart: ChartSpec;
  columns: string[];
  rows: (string | number | null)[][];
  /** 容器高度 class，默认 h-80 */
  heightClass?: string;
};

/**
 * ECharts 图表容器：init 一次、数据变化 setOption、窗口 resize 自适应。
 * 使用 echarts 内置 "dark" 主题 + 透明背景，可嵌入任意深色卡片。
 */
export default function ChartBox({
  chart,
  columns,
  rows,
  heightClass = "h-80 w-full",
}: ChartBoxProps) {
  const divRef = useRef<HTMLDivElement>(null);
  const instRef = useRef<ReturnType<typeof echarts.init> | null>(null);
  const [themeTick, setThemeTick] = useState(0);

  // 主题切换后重算轴色
  useEffect(() => {
    const obs = new MutationObserver(() => setThemeTick((t) => t + 1));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  // 主题切换后重建实例（echarts 主题在 init 时固定）；themeTick=0 时首次挂载也会跑
  useEffect(() => {
    if (!divRef.current) return;
    instRef.current?.dispose();
    const light = document.documentElement.getAttribute("data-theme") === "amber";
    const inst = echarts.init(divRef.current, light ? undefined : "dark");
    instRef.current = inst;
    const onResize = () => inst.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst.dispose();
      if (instRef.current === inst) instRef.current = null;
    };
  }, [themeTick]);

  // 数据变化时把 option 塞给图表实例
  useEffect(() => {
    const inst = instRef.current;
    if (!inst) return;
    const xIdx = columns.indexOf(chart.x_field);
    const series = chart.y_fields.map((yf) => {
      const yIdx = columns.indexOf(yf);
      return {
        name: yf,
        type: chart.type === "line" ? ("line" as const) : ("bar" as const),
        data: rows.map((r) => {
          const v = yIdx >= 0 ? r[yIdx] : null;
          const n = typeof v === "number" ? v : Number(v);
          return Number.isFinite(n) ? n : 0;
        }),
      };
    });
    // Canvas 吃不到 CSS 变量，运行时解析当前主题
    const axisColor =
      getComputedStyle(document.documentElement)
        .getPropertyValue("--chart-axis-label")
        .trim() || "#94a3b8";
    inst.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: chart.y_fields.length > 1 ? {} : undefined,
      grid: { left: 48, right: 24, top: 36, bottom: 48 },
      xAxis: {
        type: "category",
        data: rows.map((r) => (xIdx >= 0 ? String(r[xIdx] ?? "") : "")),
        axisLabel: { color: axisColor },
      },
      yAxis: { type: "value", axisLabel: { color: axisColor } },
      series,
    });
  }, [chart, columns, rows, themeTick]);

  return <div ref={divRef} className={heightClass} />;
}
