"use client";

/* 匹配结果卡（engine === "multi_agent"） */

import { ScoreRing } from "./ResultCard";
import { Badge, Section } from "./ui";
import { DIM_LABELS, type MatchFinal } from "./shared";

export default function MatchResultPanel({ final }: { final: MatchFinal }) {
  const dims = Object.entries(final.dimensions ?? {});
  const fromLlm = final.interpretation_source === "llm";

  return (
    <Section step="④" title="匹配结果" desc="简历 × 岗位 · 多 Agent 匹配分析">
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="violet">engine: {final.engine}</Badge>
          <Badge>耗时 {final.elapsed_ms} ms</Badge>
          {final.query && <Badge tone="slate">{final.query}</Badge>}
        </div>

        {/* 分数环 + 五维条 */}
        <div className="flex flex-col gap-6 md:flex-row md:items-center">
          <div className="flex flex-col items-center rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] p-4">
            <ScoreRing score={Number(final.score) || 0} />
            <p className="mt-2 text-xs text-[var(--text-muted)]">综合匹配分</p>
          </div>
          <div className="flex-1 space-y-3">
            {dims.map(([k, v]) => {
              const val = Math.max(0, Math.min(100, Number(v) || 0));
              return (
                <div key={k}>
                  <div className="mb-1 flex justify-between text-xs text-[var(--text-secondary)]">
                    <span>{DIM_LABELS[k] ?? k}</span>
                    <span className="font-mono">{val}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-[var(--chart-track)]">
                    <div
                      className="h-full rounded-full bg-[var(--accent-sky)] transition-all"
                      style={{ width: `${val}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 技能缺口 */}
        {final.skill_gap?.length > 0 && (
          <div>
            <p className="mb-2 text-sm text-[var(--text-secondary)]">技能缺口</p>
            <div className="flex flex-wrap gap-1.5">
              {final.skill_gap.map((s) => (
                <Badge key={s} tone="red">
                  {s}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* 解读 + 来源徽标 */}
        {final.interpretation && (
          <div>
            <div className="mb-2 flex items-center gap-2">
              <p className="text-sm text-[var(--text-secondary)]">结果解读</p>
              <Badge tone={fromLlm ? "sky" : "slate"}>
                {fromLlm ? "LLM 解读" : "模板文案"}
              </Badge>
            </div>
            <p className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-4 py-3 text-sm leading-relaxed text-[var(--text-primary)]">
              {final.interpretation}
            </p>
          </div>
        )}

        {/* 简历摘要 + 岗位列表 */}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] p-4">
            <p className="text-sm font-medium text-[var(--text-primary)]">
              📄 {final.resume?.filename ?? "简历"}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone="sky">resume_id: {final.resume?.resume_id}</Badge>
              {final.resume?.education && (
                <Badge>学历: {final.resume.education}</Badge>
              )}
              {final.resume?.experience_years !== undefined && (
                <Badge>经验 {final.resume.experience_years} 年</Badge>
              )}
            </div>
            {final.resume?.skills && final.resume.skills.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {final.resume.skills.map((s) => (
                  <Badge key={s} tone="green">
                    {s}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] p-4">
            <p className="text-sm font-medium text-[var(--text-primary)]">
              匹配岗位（{final.jobs?.length ?? 0}）
            </p>
            <ul className="mt-2 space-y-1.5">
              {(final.jobs ?? []).map((j) => (
                <li key={j.id} className="text-sm text-[var(--text-secondary)]">
                  <span className="text-[var(--text-primary)]">{j.title}</span>
                  <span className="text-[var(--text-muted)]">
                    {j.company ? ` · ${j.company}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </Section>
  );
}
