"use client";

/* 时间线面板：SSE 事件 → buildTimeline → TimelineCard */

import { useMemo } from "react";
import TimelineCard from "./TimelineCard";
import { buildTimeline } from "./buildTimeline";
import type { SseEvent } from "./shared";

export default function TimelinePanel({
  events,
  running,
}: {
  events: SseEvent[];
  running: boolean;
}) {
  const model = useMemo(() => buildTimeline(events), [events]);
  return (
    <div className="space-y-2">
      {/* running 时明确「执行中」，避免只有步级「运行中…」不够醒目 */}
      {running && (
        <p
          className="flex items-center gap-2 text-sm font-medium text-[var(--status-running)]"
          role="status"
        >
          <span
            className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--status-running)]"
            aria-hidden
          />
          执行中
          <span className="text-xs font-normal text-[var(--text-muted)]">
            Agent 正在处理任务，请稍候…
          </span>
        </p>
      )}
      <TimelineCard
        steps={model.steps}
        items={model.items}
        hasPlan={model.hasPlan}
        running={running}
        title={running ? "Agent 执行时间线 · 执行中" : "Agent 执行时间线"}
      />
    </div>
  );
}
