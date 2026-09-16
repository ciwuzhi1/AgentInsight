"use client";

/* 共享 UI 小组件：错误/成功条、徽标、卡内 Section、页面 Card */

import type { ReactNode } from "react";

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
    <div className="mt-3 rounded-lg border border-[var(--status-ok-border)] bg-[var(--status-ok-bg)] px-4 py-3 text-sm text-[var(--status-ok)]">
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

/** 卡内子面板：编号徽标 + 标题 + 副文案 */
export function Section({
  step,
  title,
  desc,
  children,
}: {
  step: string;
  title: string;
  desc?: string;
  children: ReactNode;
}) {
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
          <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">
            {desc}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

/** 主页板块外层卡片：小图标 + 编号 + 标题 + 一句说明 */
export function Card({
  id,
  icon,
  no,
  title,
  desc,
  children,
}: {
  id: string;
  icon: string;
  no: string;
  title: string;
  desc: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="glass scroll-mt-20 p-5 sm:p-6">
      <div className="mb-5 flex items-start gap-3 border-b border-[var(--border-glass)] pb-4">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--primary)]/10 text-xl">
          {icon}
        </span>
        <div>
          <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold text-[var(--text-primary)]">
            <span className="font-mono text-xs font-bold tracking-widest text-[var(--primary-light)]">
              {no}
            </span>
            {title}
          </h2>
          <p className="mt-0.5 text-sm text-[var(--text-secondary)]">{desc}</p>
        </div>
      </div>
      {children}
    </section>
  );
}
