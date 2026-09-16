"use client";

import { useEffect, useRef } from "react";

export type LogEntry = {
  id: string;
  time: string;
  level?: "info" | "ok" | "error" | "warn";
  text: string;
};

export function makeLog(text: string, level: LogEntry["level"] = "info"): LogEntry {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
    level,
    text,
  };
}

const LEVEL_COLOR: Record<string, string> = {
  info: "var(--text-secondary)",
  ok: "var(--status-ok)",
  error: "var(--status-error)",
  warn: "var(--status-running)",
};

export default function LogPanel({
  title = "日志",
  entries,
  className = "",
}: {
  title?: string;
  entries: LogEntry[];
  className?: string;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [entries.length]);

  return (
    <aside
      className={`glass flex h-full min-h-[200px] flex-col overflow-hidden ${className}`}
    >
      <div
        className="flex shrink-0 items-center justify-between border-b px-3 py-2"
        style={{ borderColor: "var(--border-glass)" }}
      >
        <h3 className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          {title}
        </h3>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          {entries.length} 条
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {entries.length === 0 ? (
          <p className="px-1 py-4 text-center text-[11px]" style={{ color: "var(--text-faint)" }}>
            暂无日志
          </p>
        ) : (
          <ul className="space-y-1">
            {entries.map((e) => (
              <li
                key={e.id}
                className="rounded-md px-1.5 py-1 text-[11px] leading-snug"
                style={{ color: LEVEL_COLOR[e.level ?? "info"] }}
              >
                <span className="mr-1.5 font-mono opacity-70">{e.time}</span>
                {e.text}
              </li>
            ))}
          </ul>
        )}
        <div ref={bottomRef} />
      </div>
    </aside>
  );
}
