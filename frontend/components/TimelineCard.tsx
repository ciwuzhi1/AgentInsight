"use client";

import type { ReactNode } from "react";

/* ---------- 类型（与 app/page.tsx 对齐） ---------- */

export type StepStatus = "pending" | "running" | "ok" | "error" | "skipped";

export type StepCard = {
  step_id: string;
  agent: string;
  depends_on: string[];
  status: StepStatus;
  statusText?: string;
  latency_ms?: number;
  retries: number;
};

export type TimelineItem = {
  kind: "agent" | "engine" | "sql" | "error" | "state" | "cache";
  agent?: string;
  status?: string;
  latency_ms?: number;
  engine?: string;
  rows_estimate?: number;
  reason?: string;
  sql?: string;
  explanation?: string;
  code?: string;
  message?: string;
  state_status?: string;
  hit?: boolean;
  key?: string;
};

/* ---------- 状态图标 / 徽标 ---------- */

const STATUS_META: Record<
  StepStatus,
  { icon: ReactNode; label: string; className: string; borderClass: string }
> = {
  pending: {
    icon: (
      <span
        className="inline-block h-2.5 w-2.5 rounded-full border-2"
        style={{ borderColor: "var(--status-pending)" }}
        aria-hidden
      />
    ),
    label: "待执行",
    className: "border-[var(--border-default)] bg-[var(--bg-inset)] text-[var(--text-secondary)]",
    borderClass: "border-[var(--border-default)]",
  },
  running: {
    icon: (
      <span
        className="inline-block h-2.5 w-2.5 animate-pulse rounded-full"
        style={{ backgroundColor: "var(--status-running)" }}
        aria-hidden
      />
    ),
    label: "运行中…",
    className: "border-[var(--status-running-border)] bg-[var(--status-running-bg)] text-[var(--status-running)]",
    borderClass: "border-[var(--status-running-border)]",
  },
  ok: {
    icon: (
      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden>
        <path
          d="M3 8.5l3.2 3.2L13 4.5"
          fill="none"
          stroke="var(--status-ok)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
    label: "完成",
    className: "border-[var(--status-ok-border)] bg-[var(--status-ok-bg)] text-[var(--status-ok)]",
    borderClass: "border-[var(--status-ok-border)]",
  },
  error: {
    icon: (
      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden>
        <path
          d="M4 4l8 8M12 4l-8 8"
          fill="none"
          stroke="var(--status-error)"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    ),
    label: "失败",
    className: "border-[var(--status-error-border)] bg-[var(--status-error-bg)] text-[var(--status-error)]",
    borderClass: "border-[var(--status-error-border)]",
  },
  skipped: {
    icon: (
      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden>
        <circle
          cx="8"
          cy="8"
          r="5.5"
          fill="none"
          stroke="var(--status-skipped)"
          strokeWidth="2"
          strokeDasharray="3 2"
        />
      </svg>
    ),
    label: "已跳过",
    className: "border-[var(--border-default)] bg-[var(--bg-inset)] text-[var(--text-muted)]",
    borderClass: "border-[var(--border-default)]",
  },
};

export function StatusIcon({ status }: { status: StepStatus }) {
  return STATUS_META[status]?.icon ?? null;
}

export function StatusBadge({ card }: { card: StepCard }) {
  const meta = STATUS_META[card.status];
  const label = card.status === "error" ? (card.statusText ?? meta.label) : meta.label;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${meta.className}`}
    >
      {meta.icon}
      {label}
    </span>
  );
}

/* ---------- 单步卡片 ---------- */

export function StepCardView({ card }: { card: StepCard }) {
  const borderTone = STATUS_META[card.status]?.borderClass ?? "border-[var(--border-default)]";
  return (
    <div
      className={`rounded-lg border bg-[var(--bg-inset)] px-4 py-3 ${borderTone}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-[var(--text-secondary)]">
          🤖 <span className="font-mono">{card.agent}</span>
        </span>
        <StatusBadge card={card} />
        {card.retries > 0 && (
          <span className="inline-flex items-center rounded-full border border-[var(--status-running-border)] bg-[var(--status-running-bg)] px-2 py-0.5 text-xs font-medium text-[var(--status-running)]">
            重试 ×{card.retries}
          </span>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
        <span className="font-mono">step: {card.step_id}</span>
        {card.depends_on.length > 0 && (
          <span>依赖: {card.depends_on.join("、")}</span>
        )}
        {card.latency_ms !== undefined && <span>{card.latency_ms} ms</span>}
      </div>
    </div>
  );
}

/* ---------- 时间线卡片 ---------- */

export type TimelineCardProps = {
  /** plan 事件构建出的步骤卡列表 */
  steps: StepCard[];
  /** 无 plan 旧链路 / 引擎·SQL·错误等平铺事件 */
  items?: TimelineItem[];
  /** 是否收到过 plan（决定步骤区是否渲染） */
  hasPlan?: boolean;
  /** 任务是否仍在执行（空态提示用） */
  running?: boolean;
  title?: string;
  desc?: string;
  /** 卡片左上角编号徽标（如 ③） */
  step?: string;
};

function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "slate" | "green" | "red" | "amber" | "sky" | "violet";
}) {
  const tones: Record<string, string> = {
    slate: "border-[var(--border-strong)] bg-[var(--bg-header)] text-[var(--text-secondary)]",
    green: "border-[var(--status-ok-border)] bg-[var(--status-ok-bg)] text-[var(--status-ok)]",
    red: "border-[var(--status-error-border)] bg-[var(--status-error-bg)] text-[var(--status-error)]",
    amber: "border-[var(--status-running-border)] bg-[var(--status-running-bg)] text-[var(--status-running)]",
    sky: "border-[var(--accent-sky-border)] bg-[var(--accent-sky-bg)] text-[var(--accent-sky)]",
    violet: "border-[var(--accent-violet)] bg-[color-mix(in_srgb,var(--accent-violet)_10%,transparent)] text-[var(--accent-violet)]",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * Agent 执行时间线卡片。
 * - hasPlan + steps：按 depends_on 分波次渲染步骤卡（相同依赖 → 并行列）
 * - items：引擎选择 / SQL / 错误 / 状态 / 缓存 / 旧链路 agent 平铺事件
 */
export default function TimelineCard({
  steps,
  items = [],
  hasPlan = true,
  running = false,
  title = "Agent 执行时间线",
  desc = "Agent 的每一步都会实时推送到这里；依赖相同的步骤并行执行",
  step = "③",
}: TimelineCardProps) {
  const empty = steps.length === 0 && items.length === 0;

  // depends_on 相同的相邻步骤是并行关系 → 同一波次，渲染为多列
  const waves: StepCard[][] = [];
  for (const card of steps) {
    const last = waves[waves.length - 1];
    const key = card.depends_on.join(",");
    if (last && last[0].depends_on.join(",") === key) {
      last.push(card);
    } else {
      waves.push([card]);
    }
  }

  if (empty && !running) return null;

  return (
    <section className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-panel)] p-5">
      <div className="mb-4">
        <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold text-[var(--text-primary)]">
          <span className="inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded-md bg-[var(--accent-sky-bg)] px-1.5 text-xs font-bold text-[var(--accent-sky)]">
            {step}
          </span>
          {title}
        </h3>
        {desc && (
          <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">{desc}</p>
        )}
      </div>

      {running && empty && (
        <p className="animate-pulse py-6 text-center text-sm text-[var(--text-muted)]">
          正在等待 Agent 开始执行…
        </p>
      )}

      {hasPlan && steps.length > 0 && (
        <div className="space-y-3">
          {waves.map((wave, wi) => (
            <div
              key={wi}
              className={wave.length > 1 ? "grid gap-3 md:grid-cols-2" : ""}
            >
              {wave.map((card) => (
                <StepCardView key={card.step_id} card={card} />
              ))}
            </div>
          ))}
        </div>
      )}

      {items.length > 0 && (
        <ol
          className={`space-y-3 ${
            hasPlan && steps.length > 0
              ? "mt-4 border-t border-[var(--border-subtle)] pt-4"
              : ""
          }`}
        >
          {items.map((it, idx) => {
            if (it.kind === "state") {
              return (
                <li key={idx} className="text-xs text-[var(--text-muted)]">
                  状态切换 → {it.state_status}
                </li>
              );
            }
            if (it.kind === "cache") {
              const hit = it.hit === true;
              const keyTail = it.key ? it.key.slice(-4) : "";
              return (
                <li key={idx} className="flex flex-wrap items-center gap-2 text-sm">
                  {hit ? (
                    <Badge tone="sky">
                      ⚡ Redis HIT{keyTail ? ` · …${keyTail}` : ""}
                    </Badge>
                  ) : (
                    <Badge>cache MISS</Badge>
                  )}
                </li>
              );
            }
            if (it.kind === "engine") {
              return (
                <li key={idx} className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone="violet">引擎: {it.engine}</Badge>
                  <span className="text-[var(--text-secondary)]">
                    预估行数 {it.rows_estimate?.toLocaleString()}
                    {it.reason ? `（${it.reason}）` : ""}
                  </span>
                </li>
              );
            }
            if (it.kind === "sql") {
              return (
                <li key={idx}>
                  <p className="mb-1 text-xs text-[var(--text-secondary)]">
                    {it.explanation ?? "生成的 SQL"}
                  </p>
                  <pre className="overflow-x-auto rounded-lg border border-[var(--border-default)] bg-[var(--bg-code)] px-4 py-3 font-mono text-xs text-[var(--accent-emerald)]">
                    {it.sql}
                  </pre>
                </li>
              );
            }
            if (it.kind === "error") {
              return (
                <li
                  key={idx}
                  className="rounded-lg border border-[var(--status-error-border)] bg-[var(--status-error-bg)] px-4 py-3 text-sm text-[var(--status-error)]"
                >
                  <Badge tone="red">{it.code}</Badge>
                  <span className="ml-2">{it.message}</span>
                </li>
              );
            }
            // agent（旧链路平铺）
            const ok = it.status === "ok";
            return (
              <li key={idx} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-[var(--text-secondary)]">
                  🤖 <span className="font-mono">{it.agent}</span>
                </span>
                {it.status === "running" ? (
                  <Badge tone="amber">运行中…</Badge>
                ) : ok ? (
                  <Badge tone="green">完成</Badge>
                ) : (
                  <Badge tone="red">{it.status}</Badge>
                )}
                {it.latency_ms !== undefined && (
                  <span className="text-xs text-[var(--text-muted)]">
                    {it.latency_ms} ms
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
