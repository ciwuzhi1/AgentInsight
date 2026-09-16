"use client";

/* 区块：任务历史（数据来自 MySQL 持久层，重启不丢） */

import { useState } from "react";
import type { FinalResult } from "./ResultCard";
import { Collapsible } from "./Collapsible";
import {
  API_BASE,
  errText,
  readError,
  type HistoryItem,
  type MatchFinal,
  type TraceStep,
} from "./shared";

export default function HistoryPanel({
  onReplay,
}: {
  onReplay: (
    steps: TraceStep[],
    final: MatchFinal | FinalResult | null
  ) => void;
}) {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [replaying, setReplaying] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/tasks?limit=20`);
      if (!res.ok) throw await readError(res);
      setItems(((await res.json()) as { items: HistoryItem[] }).items);
    } catch (e) {
      setError(errText(e));
    }
  }

  async function replay(taskId: string) {
    setReplaying(taskId);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/trace`);
      if (!res.ok) throw await readError(res);
      const body = (await res.json()) as {
        steps: TraceStep[];
        final_result: MatchFinal | FinalResult | null;
      };
      onReplay(body.steps ?? [], body.final_result ?? null);
    } catch (e) {
      setError(errText(e));
    } finally {
      setReplaying(null);
    }
  }

  async function download(taskId: string, fmt: "csv" | "json") {
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/tasks/${taskId}/export?format=${fmt}`
      );
      if (!res.ok) throw await readError(res);
      const blob = new Blob([await res.text()], {
        type: fmt === "csv" ? "text/csv" : "application/json",
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `task_${taskId.slice(0, 8)}.${fmt}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <Collapsible
      defaultOpen={false}
      className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-panel)] p-3"
      header={
        <h4 className="text-xs font-semibold text-[var(--text-primary)]">
          🗂 任务历史
        </h4>
      }
      headerActions={
        <button
          type="button"
          onClick={load}
          className="btn-ghost shrink-0 rounded-md border border-[var(--border-default)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)]"
        >
          {items ? "刷新" : "加载历史"}
        </button>
      }
    >
      {error && (
        <p className="mb-2 text-xs text-[var(--status-error)]">{error}</p>
      )}
      {items && items.length === 0 && (
        <p className="py-2 text-center text-xs text-[var(--text-muted)]">
          还没有任务记录——提交第一个问题吧
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="divide-y divide-[var(--border-subtle)]">
          {items.map((it) => (
            <li
              key={it.id}
              className="flex flex-wrap items-center gap-2 py-2 text-xs"
            >
              <span
                className={`rounded px-1.5 py-0.5 ${
                  it.status === "completed"
                    ? "bg-[var(--status-ok-bg)] text-[var(--status-ok)]"
                    : "bg-[var(--status-error-bg)] text-[var(--status-error)]"
                }`}
              >
                {it.status === "completed"
                  ? "完成"
                  : it.status === "failed_final"
                    ? "失败"
                    : it.status}
              </span>
              {it.engine && (
                <span className="rounded bg-[var(--bg-header)] px-1.5 py-0.5 text-[var(--text-secondary)]">
                  {it.engine}
                </span>
              )}
              {typeof it.score === "number" && (
                <span className="rounded bg-[var(--accent-sky-bg)] px-1.5 py-0.5 text-[var(--accent-sky)]">
                  {it.score} 分
                </span>
              )}
              <span
                className="min-w-0 flex-1 truncate text-[var(--text-secondary)]"
                title={it.query}
              >
                {it.query}
              </span>
              <span className="text-[var(--text-muted)]">{it.created_at}</span>
              <button
                onClick={() => replay(it.id)}
                disabled={replaying === it.id}
                className="btn-ghost rounded border border-[var(--border-default)] px-2 py-0.5 text-[var(--text-secondary)] disabled:opacity-50"
              >
                {replaying === it.id ? "回放中…" : "回放"}
              </button>
              <button
                onClick={() => download(it.id, "csv")}
                className="btn-ghost rounded border border-[var(--border-default)] px-2 py-0.5 text-[var(--text-secondary)]"
              >
                CSV
              </button>
              <button
                onClick={() => download(it.id, "json")}
                className="btn-ghost rounded border border-[var(--border-default)] px-2 py-0.5 text-[var(--text-secondary)]"
              >
                JSON
              </button>
            </li>
          ))}
        </ul>
      )}
    </Collapsible>
  );
}
