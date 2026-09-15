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
    <TimelineCard
      steps={model.steps}
      items={model.items}
      hasPlan={model.hasPlan}
      running={running}
    />
  );
}
