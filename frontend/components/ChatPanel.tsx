"use client";

/* 区块② 提交岗位问题：预设问题 chips + InputBar 输入 */

import { useEffect, useState } from "react";
import { InputBar } from "./InputBar";
import { ErrorBar, Section } from "./ui";
import { JOB_PROMPT_PRESETS, type DatasetInfo } from "./shared";

/** 兼容别名：单一来源在 shared.ts */
export { JOB_PROMPT_PRESETS };

/** 派发导航意图：侧栏若未监听也不报错（无人监听的 dispatchEvent 为 no-op） */
function requestDatasetView() {
  try {
    window.dispatchEvent(
      new CustomEvent("agentinsight:navigate", { detail: { view: "dataset" } })
    );
  } catch {
    /* SSR / 异常环境静默 */
  }
}

export default function ChatPanel({
  dataset,
  running,
  onStartTask,
  suggestedPrompt,
}: {
  dataset: DatasetInfo | null;
  running: boolean;
  onStartTask: (query: string) => void;
  suggestedPrompt?: string | null;
}) {
  const [preset, setPreset] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (suggestedPrompt) setPreset(suggestedPrompt);
  }, [suggestedPrompt]);

  function handleSend(text: string) {
    if (!dataset) {
      setError("请先在「数据集」页上传 CSV");
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
      collapsible
      defaultOpen={true}
    >
      {/* 空状态引导：dataset 为空时在面板顶部提示并可跳转数据集页 */}
      {!dataset && (
        <div
          className="mb-3 flex flex-col gap-2 rounded-lg border border-[var(--accent-sky-border)] bg-[var(--accent-sky-bg)] px-4 py-3 text-sm"
          role="status"
        >
          <div className="flex items-start gap-2 text-[var(--accent-sky)]">
            <span aria-hidden className="mt-0.5 leading-none">
              ⓘ
            </span>
            <div className="flex-1">
              <p className="font-medium">请先到左侧「数据集」上传 CSV</p>
              <p className="mt-1 text-xs opacity-80">
                上传成功后即可在本面板提交岗位问题，Agent 将自动翻译为 SQL 执行。
              </p>
            </div>
          </div>
          <div>
            <button
              type="button"
              onClick={requestDatasetView}
              className="btn-ghost rounded-md border border-[var(--accent-sky-border)] bg-[var(--bg-panel)] px-3 py-1 text-xs text-[var(--accent-sky)] transition hover:brightness-110"
            >
              去「数据集」页上传 →
            </button>
          </div>
        </div>
      )}

      {/* 有 dataset 时：短提示当前 dataset_id */}
      {dataset && (
        <p className="mb-2 text-[11px] text-[var(--text-muted)]">
          当前数据集：
          <span className="font-mono text-[var(--text-secondary)]">
            {dataset.dataset_id.length > 12
              ? `${dataset.dataset_id.slice(0, 8)}…`
              : dataset.dataset_id}
          </span>
        </p>
      )}

      {/* 预设问题 chips */}
      <div className="mb-2">
        <p className="mb-1 text-[11px] text-[var(--text-muted)]">常用问题（点击填入）：</p>
        <div className="flex flex-wrap gap-1">
          {JOB_PROMPT_PRESETS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => setPreset(q)}
              disabled={!dataset || running}
              className={`max-w-full truncate rounded-full border px-2 py-0.5 text-[11px] transition disabled:cursor-not-allowed disabled:opacity-40 ${
                preset === q
                  ? "border-[var(--primary)] bg-[var(--primary)]/15 text-[var(--primary-light)]"
                  : "border-[var(--border-default)] bg-[var(--bg-inset)] text-[var(--text-secondary)] hover:border-[var(--primary)]/50 hover:text-[var(--text-primary)]"
              }`}
              title={q}
            >
              {q}
            </button>
          ))}
        </div>
      </div>

      {/* 输入栏：使用 InputBar（Enter 发送，Shift+Enter 换行）；无 dataset 时 disabled */}
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

      <p className="mt-2 text-[11px] text-[var(--text-muted)]">
        建议搭配 <span className="font-mono">jd_large.csv</span>
      </p>

      {/* 运行中提示 */}
      {running && (
        <p className="mt-3 flex items-center gap-2 text-xs text-[var(--status-running)]">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[var(--status-running)]" />
          Agent 分析中，请稍候…
        </p>
      )}

      {/* 无 dataset 时提交 → 错误文案（已指向数据集页） */}
      {error && <ErrorBar message={error} />}
    </Section>
  );
}
