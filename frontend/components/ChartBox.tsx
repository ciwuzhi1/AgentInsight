"use client";

import { useEffect, useRef } from "react";
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

  // 挂载时 init 一次，卸载时 dispose
  useEffect(() => {
    if (!divRef.current) return;
    const inst = echarts.init(divRef.current, "dark");
    instRef.current = inst;
    const onResize = () => inst.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst.dispose();
      instRef.current = null;
    };
  }, []);

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
    // Canvas 渲染器无法解析 CSS 变量，轴标签用字面色值
    const axisColor = "#94a3b8"; // var(--chart-axis-label)
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
  }, [chart, columns, rows]);

  return <div ref={divRef} className={heightClass} />;
}
