"use client";

/* 共享 UI 小组件：错误/成功条、徽标、卡内 Section */

import { useState, type ReactNode } from "react";

function CollapseChevron({ open }: { open: boolean }) {
  return (
    <span
      className="chevron ml-auto inline-flex h-4 w-4 shrink-0 items-center justify-center text-[var(--text-secondary)]"
      data-open={open}
      aria-hidden
    >
      <svg viewBox="0 0 16 16" className="h-3 w-3">
        <path
          d="M6 3.5L10.5 8 6 12.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

export function ErrorBar({
  message,
  retrying,
}: {
  message: string;
  retrying?: boolean;
}) {
  return (
    <div
      className={`mt-3 flex items-start gap-3 rounded-lg border px-4 py-3 text-sm ${
        retrying
          ? "border-[var(--status-running-border)] bg-[var(--status-running-bg)] text-[var(--status-running)]"
          : "border-[var(--status-error-border)] bg-[var(--status-error-bg)] text-[var(--status-error)]"
      }`}
      role="alert"
    >
      <span className="mt-0.5 text-lg leading-none">{retrying ? "⚠" : "✕"}</span>
      <div className="flex-1">
        <p className="font-medium">
          {retrying ? "执行出错，正在重试…" : "任务失败"}
        </p>
        <p className="mt-1 text-xs opacity-80">{message}</p>
      </div>
    </div>
  );
}

export function OkBar({ message }: { message: string }) {
  return (
    <div
      role="status"
      className="mt-3 rounded-lg border border-[var(--status-ok-border)] bg-[var(--status-ok-bg)] px-4 py-3 text-sm text-[var(--status-ok)]"
    >
      {message}
    </div>
  );
}

export function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: "slate" | "green" | "red" | "amber" | "sky" | "violet";
}) {
  const tones: Record<string, string> = {
    slate:
      "border-[var(--border-strong)] bg-[var(--bg-header)] text-[var(--text-secondary)]",
    green:
      "border-[var(--status-ok-border)] bg-[var(--status-ok-bg)] text-[var(--status-ok)]",
    red: "border-[var(--status-error-border)] bg-[var(--status-error-bg)] text-[var(--status-error)]",
    amber:
      "border-[var(--status-running-border)] bg-[var(--status-running-bg)] text-[var(--status-running)]",
    sky: "border-[var(--accent-sky-border)] bg-[var(--accent-sky-bg)] text-[var(--accent-sky)]",
    violet:
      "border-[var(--accent-violet)] bg-[color-mix(in_srgb,var(--accent-violet)_10%,transparent)] text-[var(--accent-violet)]",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** 卡内子面板：编号徽标 + 标题 + 副文案，标题行可折叠 */
export function Section({
  step,
  title,
  desc,
  children,
  collapsible = true,
  defaultOpen = true,
  open: controlledOpen,
  onOpenChange,
}: {
  step: string;
  title: string;
  desc?: string;
  children: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const open = collapsible ? (controlledOpen ?? internalOpen) : true;

  function toggle() {
    if (!collapsible) return;
    const next = !open;
    if (controlledOpen === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  }

  return (
    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-panel)] p-3">
      <div className="mb-2">
        <button
          type="button"
          onClick={toggle}
          disabled={!collapsible}
          className={`flex w-full items-center gap-2 text-left ${
            collapsible ? "collapse-header" : ""
          }`}
          aria-expanded={collapsible ? open : undefined}
        >
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-[var(--text-primary)]">
              <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded bg-[var(--accent-sky-bg)] px-1 text-[11px] font-bold text-[var(--accent-sky)]">
                {step}
              </span>
              {title}
            </span>
            {desc && (
              <span className="mt-0.5 block text-[11px] leading-snug text-[var(--text-muted)]">
                {desc}
              </span>
            )}
          </span>
          {collapsible && <CollapseChevron open={open} />}
        </button>
      </div>
      {collapsible ? (
        <div
          className="collapse-grid"
          data-open={open}
          style={{
            display: "grid",
            gridTemplateRows: open ? "1fr" : "0fr",
            transition: "grid-template-rows 0.22s ease",
          }}
        >
          <div style={{ overflow: "hidden", minHeight: 0 }}>{children}</div>
        </div>
      ) : (
        children
      )}
    </section>
  );
}


