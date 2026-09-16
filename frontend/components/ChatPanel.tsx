"use client";

/* 区块② 提交岗位问题：预设问题 chips + InputBar 输入 */

import { useState } from "react";
import { InputBar } from "./InputBar";
import { ErrorBar, Section } from "./ui";
import type { DatasetInfo } from "./shared";

/* 岗位问题提示词预设：点击仅填入输入框，可编辑后再提交 */
export const JOB_PROMPT_PRESETS = [
  "JD 中需求最多的技能 Top 10 是什么？",
  "统计各城市的岗位数量和平均薪资，按数量降序",
  "要求 Python 的岗位里，哪个城市平均薪资最高？",
  "各薪资区间（salary_k）的岗位数量分布",
  "'Python+SQL' 同时出现的岗位有多少？",
  "对比北京和上海岗位的技能要求差异",
  "最近发布（2025-05 之后）的岗位最常见 5 项技能",
  "岗位数量最多的公司 Top 5",
];

export default function ChatPanel({
  dataset,
  running,
  onStartTask,
}: {
  dataset: DatasetInfo | null;
  running: boolean;
  onStartTask: (query: string) => void;
}) {
  const [preset, setPreset] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleSend(text: string) {
    if (!dataset) {
      setError("请先在上方上传数据集");
      return;
    }
    setError(null);
    setPreset(null);
    onStartTask(text);
  }

  return (
    <Section
      step="②"
      title="提交岗位问题（提示词）"
      desc="Agent 会把你的岗位问题翻译成 SQL 并在引擎上执行，全过程实时可见"
    >
      {/* 预设问题 chips */}
      <div className="mb-3">
        <p className="mb-2 text-xs text-[var(--text-muted)]">
          常用岗位问题（点击填入，可修改后再提交）：
        </p>
        <div className="flex flex-wrap gap-2">
          {JOB_PROMPT_PRESETS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => setPreset(q)}
              disabled={!dataset || running}
              className={`rounded-full border px-3 py-1.5 text-xs transition disabled:cursor-not-allowed disabled:opacity-40 ${
                preset === q
                  ? "border-[var(--primary)] bg-[var(--primary)]/15 text-[var(--primary-light)]"
                  : "border-[var(--border-default)] bg-[var(--bg-inset)] text-[var(--text-secondary)] hover:border-[var(--primary)]/50 hover:text-[var(--text-primary)]"
              }`}
            >
              {q}
            </button>
          ))}
        </div>
      </div>

      {/* 输入栏：使用 InputBar（Enter 发送，Shift+Enter 换行） */}
      <InputBar
        key={preset ?? "empty"}
        defaultValue={preset ?? ""}
        onSend={handleSend}
        disabled={!dataset || running}
        placeholder={
          dataset
            ? "例如：统计各城市的岗位数量和平均薪资，按数量降序"
            : "请先上传数据集，再在这里输入问题…"
        }
      />

      <p className="mt-3 text-xs text-[var(--text-muted)]">
        提示词预设与数据集无硬绑定——上传什么数据集由你决定，建议搭配
        <span className="font-mono text-[var(--text-secondary)]">
          {" "}
          jd_large.csv
        </span>{" "}
        使用（字段：job_id, title, company, city, salary_k, skills,
        posted_date）。
      </p>

      {/* 运行中提示 */}
      {running && (
        <p className="mt-3 flex items-center gap-2 text-xs text-[var(--status-running)]">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--status-running)]" />
          Agent 分析中，请稍候…
        </p>
      )}

      {error && <ErrorBar message={error} />}
    </Section>
  );
}
