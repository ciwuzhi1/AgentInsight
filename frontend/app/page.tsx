"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import TimelineCard, {
  type StepCard,
  type TimelineItem,
} from "@/components/TimelineCard";
import ResultCard, {
  ScoreRing,
  type FinalResult,
} from "@/components/ResultCard";
import { getToken, installAuthFetch, sseUrl, UserChip } from "./auth-client";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

installAuthFetch();

/* 岗位问题提示词预设：点击仅填入输入框，可编辑后再提交（与数据集无硬绑定） */
const JOB_PROMPT_PRESETS = [
  "JD 中需求最多的技能 Top 10 是什么？",
  "统计各城市的岗位数量和平均薪资，按数量降序",
  "要求 Python 的岗位里，哪个城市平均薪资最高？",
  "各薪资区间（salary_k）的岗位数量分布",
  "'Python+SQL' 同时出现的岗位有多少？",
  "对比北京和上海岗位的技能要求差异",
  "最近发布（2025-05 之后）的岗位最常见 5 项技能",
  "岗位数量最多的公司 Top 5",
];

/* ---------- 类型 ---------- */

type SchemaCol = { name: string; type: string };

type DatasetInfo = {
  dataset_id: string;
  name: string;
  table_name: string;
  schema: SchemaCol[];
  rows_estimate: number;
  size_mb: number;
  engine_hint: string;
};

type SseEvent = { type: string } & Record<string, unknown>;

/* plan 事件里的一个步骤声明 */
type PlanStepInfo = { id: string; agent: string; depends_on: string[] };

type TimelineModel = {
  hasPlan: boolean;
  steps: StepCard[];
  items: TimelineItem[];
};

/* 匹配形态 final（CONTRACTS2 §2.1） */
type MatchFinal = {
  task_id: string;
  query: string;
  engine: string;
  elapsed_ms: number;
  resume: {
    resume_id: string;
    filename: string;
    skills?: string[];
    experience_years?: number;
    education?: string;
  };
  jobs: { id: number; title: string; company: string | null }[];
  score: number;
  dimensions: Record<string, number>;
  skill_gap: string[];
  interpretation: string;
  interpretation_source: string;
};

type JobItem = {
  title: string;
  company: string | null;
  location: string | null;
  skills: string[];
};

/* 爬虫岗位库里的岗位（带 id，供匹配多选） */
type CrawlerJobItem = JobItem & { id: number };

const DIM_LABELS: Record<string, string> = {
  skill: "技能",
  project: "项目",
  experience: "经验",
  education: "学历",
  engineering: "工程",
};

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function readError(res: Response): Promise<Error> {
  const detail = await res.json().catch(() => ({}));
  const d = (detail as { detail?: string }).detail;
  return new Error(d ?? `HTTP ${res.status}`);
}

/* ---------- 小组件 ---------- */

function ErrorBar({ message, retrying }: { message: string; retrying?: boolean }) {
  return (
    <div
      className={`mt-3 flex items-start gap-3 rounded-lg border px-4 py-3 text-sm ${
        retrying
          ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
          : "border-red-500/50 bg-red-500/15 text-red-200 shadow-lg shadow-red-500/10"
      }`}
    >
      <span className="mt-0.5 text-lg leading-none">{retrying ? "⚠" : "✕"}</span>
      <div className="flex-1">
        <p className="font-medium">{retrying ? "执行出错，正在重试…" : "任务失败"}</p>
        <p className="mt-1 text-xs opacity-80">{message}</p>
      </div>
    </div>
  );
}

function OkBar({ message }: { message: string }) {
  return (
    <div className="mt-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
      {message}
    </div>
  );
}

function Badge({
  children,
  tone = "slate",
}: {
  children: React.ReactNode;
  tone?: "slate" | "green" | "red" | "amber" | "sky" | "violet";
}) {
  const tones: Record<string, string> = {
    slate: "border-slate-600 bg-slate-800 text-slate-300",
    green: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    red: "border-red-500/40 bg-red-500/10 text-red-300",
    amber: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    sky: "border-sky-500/40 bg-sky-500/10 text-sky-300",
    violet: "border-violet-500/40 bg-violet-500/10 text-violet-300",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/* 卡内子面板：编号徽标 + 标题 + 副文案（统一样式的 Section 标题） */
function Section({
  step,
  title,
  desc,
  children,
}: {
  step: string;
  title: string;
  desc?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-800/70 bg-slate-950/40 p-5">
      <div className="mb-4">
        <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold text-slate-100">
          <span className="inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded-md bg-sky-500/15 px-1.5 text-xs font-bold text-sky-400">
            {step}
          </span>
          {title}
        </h3>
        {desc && <p className="mt-1 text-xs leading-relaxed text-slate-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

/* 主页板块外层卡片：小图标 + 编号 + 标题 + 一句说明 */
function Card({
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
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className="scroll-mt-20 glass p-5 sm:p-6"
    >
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

/* ---------- 区块① 数据集上传 ---------- */

function UploadPanel({
  dataset,
  setDataset,
}: {
  dataset: DatasetInfo | null;
  setDataset: (d: DatasetInfo | null) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");

  async function handleUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError("请先选择一个 .csv 文件");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/datasets`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) throw await readError(res);
      setDataset((await res.json()) as DatasetInfo);
    } catch (e) {
      setError(errText(e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <Section
      step="①"
      title="数据集上传"
      desc="选择一个 CSV 文件上传，系统会自动分析它的结构与规模"
    >
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          onChange={(e) => setFileName(e.target.files?.[0]?.name ?? "")}
          className="block w-full max-w-sm cursor-pointer rounded-lg border border-slate-700 bg-slate-800/60 py-2 px-3 text-sm text-slate-300 file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-sky-600 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white hover:file:bg-sky-500"
        />
        <button
          onClick={handleUpload}
          disabled={uploading}
          className="rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? "上传中…" : "上传"}
        </button>
        {fileName && (
          <span className="text-sm text-slate-400">已选择：{fileName}</span>
        )}
      </div>

      {error && <ErrorBar message={error} />}

      {dataset && (
        <div className="mt-5 space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone="sky">dataset_id: {dataset.dataset_id}</Badge>
            <Badge>行数 ≈ {dataset.rows_estimate.toLocaleString()}</Badge>
            <Badge>{dataset.size_mb.toFixed(2)} MB</Badge>
            <Badge tone="violet">推荐引擎: {dataset.engine_hint}</Badge>
          </div>
          <div className="overflow-hidden rounded-lg border border-slate-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-800/80 text-slate-300">
                <tr>
                  <th className="px-4 py-2 font-medium">列名</th>
                  <th className="px-4 py-2 font-medium">类型</th>
                </tr>
              </thead>
              <tbody>
                {dataset.schema.map((col) => (
                  <tr
                    key={col.name}
                    className="border-t border-slate-800 text-slate-300"
                  >
                    <td className="px-4 py-1.5 font-mono">{col.name}</td>
                    <td className="px-4 py-1.5 text-slate-400">{col.type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Section>
  );
}

/* ---------- 区块② 提问 ---------- */

function ChatPanel({
  dataset,
  running,
  onStartTask,
}: {
  dataset: DatasetInfo | null;
  running: boolean;
  onStartTask: (query: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit() {
    const text = query.trim();
    if (!text) {
      setError("请输入问题");
      return;
    }
    if (!dataset) {
      setError("请先在上方上传数据集");
      return;
    }
    setError(null);
    setQuery(text);
    onStartTask(text);
  }

  return (
    <Section
      step="②"
      title="提交岗位问题（提示词）"
      desc="Agent 会把你的岗位问题翻译成 SQL 并在引擎上执行，全过程实时可见"
    >
      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        rows={2}
        placeholder={
          dataset
            ? "例如：统计各城市的岗位数量和平均薪资，按数量降序"
            : "请先上传数据集，再在这里输入问题…"
        }
        className="w-full resize-none rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
      />
      <div className="mt-3">
        <p className="mb-2 text-xs text-slate-500">
          常用岗位问题（点击填入，可修改后再提交）：
        </p>
        <div className="flex flex-wrap gap-2">
          {JOB_PROMPT_PRESETS.map((q) => (
            <button
              key={q}
              onClick={() => setQuery(q)}
              className="rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-300 transition hover:border-sky-500/60 hover:text-sky-300"
            >
              {q}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        提示词预设与数据集无硬绑定——上传什么数据集由你决定，建议搭配
        <span className="font-mono text-slate-400"> jd_large.csv</span>{" "}
        使用（字段：job_id, title, company, city, salary_k, skills, posted_date）。
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => submit()}
          disabled={running}
          className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "Agent 分析中…" : "提交问题"}
        </button>
      </div>
      {error && <ErrorBar message={error} />}
    </Section>
  );
}

/* ---------- 区块③ Agent 执行时间线（plan steps 分组模型） ---------- */

function buildTimeline(events: SseEvent[]): TimelineModel {
  let hasPlan = false;
  let steps: StepCard[] = [];
  const byId = new Map<string, StepCard>();
  const items: TimelineItem[] = [];

  const ensureCard = (stepId: string, agent: string): StepCard => {
    let card = byId.get(stepId);
    if (!card) {
      card = {
        step_id: stepId,
        agent,
        depends_on: [],
        status: "running",
        retries: 0,
      };
      byId.set(stepId, card);
      steps.push(card);
    }
    return card;
  };

  // 优先按 step id 配对；没有 step id 时退化为按 agent 名找运行中的卡
  const matchCard = (ev: SseEvent): StepCard | undefined => {
    const stepId = ev.step !== undefined && ev.step !== null ? String(ev.step) : "";
    if (stepId) {
      const card = byId.get(stepId);
      if (card) return card;
    }
    const agent = String(ev.agent ?? "");
    return steps.find((s) => s.agent === agent);
  };

  for (const ev of events) {
    switch (ev.type) {
      case "plan": {
        hasPlan = true;
        steps = [];
        byId.clear();
        const raw = Array.isArray(ev.steps)
          ? (ev.steps as Record<string, unknown>[])
          : [];
        for (const s of raw) {
          const card: StepCard = {
            step_id: String(s.id ?? ""),
            agent: String(s.agent ?? ""),
            depends_on: Array.isArray(s.depends_on)
              ? (s.depends_on as unknown[]).map(String)
              : [],
            status: "pending",
            retries: 0,
          };
          byId.set(card.step_id, card);
          steps.push(card);
        }
        break;
      }
      case "agent_start": {
        if (hasPlan) {
          const stepId =
            ev.step !== undefined && ev.step !== null ? String(ev.step) : "";
          const card = ensureCard(stepId || String(ev.agent ?? ""), String(ev.agent ?? ""));
          card.status = "running";
        } else {
          // 旧链路（无 plan）：平铺
          items.push({
            kind: "agent",
            agent: String(ev.agent ?? ""),
            status: "running",
          });
        }
        break;
      }
      case "agent_end": {
        const status = String(ev.status ?? "ok");
        const latency = Number(ev.latency_ms ?? 0);
        if (hasPlan) {
          const card = matchCard(ev);
          if (card) {
            card.status = status === "ok" ? "ok" : "error";
            card.statusText = status === "ok" ? undefined : status;
            card.latency_ms = latency;
          }
        } else {
          const target = [...items]
            .reverse()
            .find(
              (i) =>
                i.kind === "agent" &&
                i.agent === ev.agent &&
                i.status === "running"
            );
          if (target) {
            target.status = status;
            target.latency_ms = latency;
          } else {
            items.push({
              kind: "agent",
              agent: String(ev.agent ?? ""),
              status,
              latency_ms: latency,
            });
          }
        }
        break;
      }
      case "retry": {
        if (hasPlan) {
          const card = matchCard(ev);
          if (card) {
            card.retries = Math.max(card.retries, Number(ev.retry_count ?? 0) || card.retries + 1);
          }
        }
        break;
      }
      case "step_skipped": {
        if (hasPlan) {
          const stepId = ev.step !== undefined && ev.step !== null ? String(ev.step) : "";
          const card = stepId
            ? byId.get(stepId)
            : steps.find((s) => s.agent === String(ev.agent ?? ""));
          if (card) {
            card.status = "skipped";
          }
        }
        break;
      }
      case "engine":
        items.push({
          kind: "engine",
          engine: String(ev.engine ?? ""),
          rows_estimate: Number(ev.rows_estimate ?? 0),
          reason: ev.reason ? String(ev.reason) : undefined,
        });
        break;
      case "sql":
        items.push({
          kind: "sql",
          sql: String(ev.sql ?? ""),
          explanation: ev.explanation ? String(ev.explanation) : undefined,
        });
        break;
      case "error":
        items.push({
          kind: "error",
          code: String(ev.code ?? "ERROR"),
          message: String(ev.message ?? ""),
        });
        break;
      case "state":
        items.push({ kind: "state", state_status: String(ev.status ?? "") });
        break;
      case "cache":
        items.push({
          kind: "cache",
          hit: ev.hit === true,
          key: ev.key !== undefined && ev.key !== null ? String(ev.key) : undefined,
        });
        break;
      default:
        break;
    }
  }
  return { hasPlan, steps, items };
}

function TimelinePanel({
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

/* ---------- 匹配结果卡（engine === "multi_agent"） ---------- */

function MatchResultPanel({ final }: { final: MatchFinal }) {
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
          <div className="flex flex-col items-center rounded-lg border border-slate-800 bg-slate-950/40 p-4">
            <ScoreRing score={Number(final.score) || 0} />
            <p className="mt-2 text-xs text-slate-500">综合匹配分</p>
          </div>
          <div className="flex-1 space-y-3">
            {dims.map(([k, v]) => {
              const val = Math.max(0, Math.min(100, Number(v) || 0));
              return (
                <div key={k}>
                  <div className="mb-1 flex justify-between text-xs text-slate-400">
                    <span>{DIM_LABELS[k] ?? k}</span>
                    <span className="font-mono">{val}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className="h-full rounded-full bg-sky-500 transition-all"
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
            <p className="mb-2 text-sm text-slate-400">技能缺口</p>
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
              <p className="text-sm text-slate-400">结果解读</p>
              <Badge tone={fromLlm ? "sky" : "slate"}>
                {fromLlm ? "LLM 解读" : "模板文案"}
              </Badge>
            </div>
            <p className="rounded-lg border border-slate-800 bg-slate-800/40 px-4 py-3 text-sm leading-relaxed text-slate-200">
              {final.interpretation}
            </p>
          </div>
        )}

        {/* 简历摘要 + 岗位列表 */}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-4">
            <p className="text-sm font-medium text-slate-200">
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
          <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-4">
            <p className="text-sm font-medium text-slate-200">
              匹配岗位（{final.jobs?.length ?? 0}）
            </p>
            <ul className="mt-2 space-y-1.5">
              {(final.jobs ?? []).map((j) => (
                <li key={j.id} className="text-sm text-slate-300">
                  <span className="text-slate-200">{j.title}</span>
                  <span className="text-slate-500">
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

/* ---------- 区块⑤ 简历匹配 ---------- */

function MatchPanel({
  running,
  onStartMatch,
}: {
  running: boolean;
  onStartMatch: (resumeId: string, jobIds: number[]) => void;
}) {
  const [resume, setResume] = useState<{
    resume_id: string;
    filename: string;
  } | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");

  const [jobs, setJobs] = useState<CrawlerJobItem[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  // 打开页面即拉取岗位库（数据来自爬虫面板）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setJobsLoading(true);
      setJobsError(null);
      try {
        const res = await fetch(`${API_BASE}/api/crawler/jobs?limit=50`);
        if (!res.ok) throw await readError(res);
        const data = (await res.json()) as { items?: CrawlerJobItem[] };
        if (!cancelled) setJobs(Array.isArray(data.items) ? data.items : []);
      } catch (e) {
        if (!cancelled) setJobsError(errText(e));
      } finally {
        if (!cancelled) setJobsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function uploadResume() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError("请先选择简历文件（pdf / docx / txt）");
      return;
    }
    setUploading(true);
    setError(null);
    setOkMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/resumes`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) throw await readError(res);
      const data = (await res.json()) as {
        resume_id: string;
        filename: string;
      };
      setResume(data);
      setOkMsg(`简历已上传（重复上传会覆盖之前的选择）`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setUploading(false);
    }
  }

  function toggleJob(id: number) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 5) {
        setError("最多选择 5 个岗位");
        return prev;
      }
      setError(null);
      return [...prev, id];
    });
  }

  function startMatch() {
    setError(null);
    setOkMsg(null);
    if (!resume) {
      setError("请先上传简历");
      return;
    }
    if (selected.length === 0) {
      setError("请至少选择一个岗位");
      return;
    }
    onStartMatch(resume.resume_id, selected);
  }

  return (
    <Section
      step="⑤"
      title="简历匹配"
      desc="上传简历，选择岗位（数据来自下方爬虫面板），由多 Agent 打分与解读"
    >
      <div className="grid gap-6 md:grid-cols-2">
        {/* 左半：上传简历 */}
        <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-4">
          <p className="mb-3 text-sm font-medium text-slate-200">上传简历</p>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt"
              onChange={(e) => setFileName(e.target.files?.[0]?.name ?? "")}
              className="block w-full cursor-pointer rounded-lg border border-slate-700 bg-slate-800/60 py-2 px-3 text-sm text-slate-300 file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-sky-600 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white hover:file:bg-sky-500"
            />
            <button
              onClick={uploadResume}
              disabled={uploading}
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {uploading ? "上传中…" : "上传简历"}
            </button>
          </div>
          {fileName && (
            <p className="mt-2 text-xs text-slate-500">已选择：{fileName}</p>
          )}
          {resume && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="sky">resume_id: {resume.resume_id}</Badge>
              <Badge tone="green">{resume.filename}</Badge>
            </div>
          )}
          <p className="mt-3 text-xs text-slate-500">
            支持 pdf / docx / txt，最大 10MB；再次上传会覆盖之前的简历。
          </p>
        </div>

        {/* 右半：选择岗位 */}
        <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-slate-200">
              选择岗位（最多 5 个，已选 {selected.length}）
            </p>
            {selected.length > 0 && (
              <button
                onClick={() => setSelected([])}
                className="text-xs text-slate-400 transition hover:text-slate-200"
              >
                清空
              </button>
            )}
          </div>
          {jobsLoading && (
            <p className="animate-pulse text-sm text-slate-400">
              正在加载岗位库…
            </p>
          )}
          {jobsError && <ErrorBar message={jobsError} />}
          {!jobsLoading && !jobsError && jobs.length === 0 && (
            <p className="rounded-lg border border-dashed border-slate-800 py-6 text-center text-sm text-slate-500">
              岗位库还是空的：请先在下方爬虫面板抓取岗位，然后刷新本页。
            </p>
          )}
          {jobs.length > 0 && (
            <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
              {jobs.map((job) => (
                <li key={job.id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 text-sm transition hover:border-slate-700 hover:bg-slate-800/60">
                    <input
                      type="checkbox"
                      checked={selected.includes(job.id)}
                      onChange={() => toggleJob(job.id)}
                      className="mt-0.5 h-4 w-4 accent-sky-500"
                    />
                    <span>
                      <span className="text-slate-200">{job.title}</span>
                      <span className="text-slate-500">
                        {job.company ? ` · ${job.company}` : ""}
                        {job.location ? ` · ${job.location}` : ""}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {error && <ErrorBar message={error} />}
      {okMsg && !error && <OkBar message={okMsg} />}

      <div className="mt-4">
        <button
          onClick={startMatch}
          disabled={running}
          className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "匹配进行中…" : "开始匹配"}
        </button>
        <span className="ml-3 text-xs text-slate-500">
          匹配过程与结果复用上方的执行时间线和结果区
        </span>
      </div>
    </Section>
  );
}

/* ---------- 区块：任务历史（P6，数据来自 MySQL 持久层，重启不丢） ---------- */

type TraceStep = {
  agent_name: string;
  status: string;
  latency_ms: number | null;
  detail: Record<string, unknown> | null;
};

type HistoryItem = {
  id: string;
  query: string;
  status: string;
  engine: string | null;
  score: number | null;
  created_at: string;
};

function HistoryPanel({
  onReplay,
}: {
  onReplay: (steps: TraceStep[], final: MatchFinal | FinalResult | null) => void;
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
      const body = (await res.json()) as { steps: TraceStep[]; final_result: MatchFinal | FinalResult | null };
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
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/export?format=${fmt}`);
      if (!res.ok) throw await readError(res);
      const blob = new Blob([await res.text()], {
        type: fmt === "csv" ? "text/csv" : "application/json",
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `task_${taskId.slice(0, 8)}.${fmt}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-semibold text-slate-200">🗂 任务历史（MySQL 持久，重启不丢）</h4>
        <button
          onClick={load}
          className="rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-300 transition hover:border-sky-500/60 hover:text-sky-300"
        >
          {items ? "刷新" : "加载历史"}
        </button>
      </div>
      {error && <p className="mb-2 text-xs text-rose-400">{error}</p>}
      {items && items.length === 0 && (
        <p className="py-2 text-center text-xs text-slate-500">还没有任务记录——提交第一个问题吧</p>
      )}
      {items && items.length > 0 && (
        <ul className="divide-y divide-slate-800/70">
          {items.map((it) => (
            <li key={it.id} className="flex flex-wrap items-center gap-2 py-2 text-xs">
              <span
                className={`rounded px-1.5 py-0.5 ${
                  it.status === "completed"
                    ? "bg-emerald-900/50 text-emerald-300"
                    : "bg-rose-900/50 text-rose-300"
                }`}
              >
                {it.status === "completed" ? "完成" : it.status === "failed_final" ? "失败" : it.status}
              </span>
              {it.engine && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">{it.engine}</span>}
              {typeof it.score === "number" && (
                <span className="rounded bg-sky-900/50 px-1.5 py-0.5 text-sky-300">{it.score} 分</span>
              )}
              <span className="min-w-0 flex-1 truncate text-slate-300" title={it.query}>
                {it.query}
              </span>
              <span className="text-slate-500">{it.created_at}</span>
              <button
                onClick={() => replay(it.id)}
                disabled={replaying === it.id}
                className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 transition hover:border-sky-500/60 hover:text-sky-300 disabled:opacity-50"
              >
                {replaying === it.id ? "回放中…" : "回放"}
              </button>
              <button
                onClick={() => download(it.id, "csv")}
                className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 transition hover:border-amber-500/60 hover:text-amber-300"
              >
                CSV
              </button>
              <button
                onClick={() => download(it.id, "json")}
                className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 transition hover:border-amber-500/60 hover:text-amber-300"
              >
                JSON
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------- 区块⑥ 爬虫面板 ---------- */

function CrawlerPanel() {
  const [url, setUrl] = useState("");
  const [pages, setPages] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stat, setStat] = useState<{ inserted: number; skipped: number } | null>(
    null
  );
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [exported, setExported] = useState<{ path: string; rows: number } | null>(
    null
  );

  async function run() {
    setBusy(true);
    setError(null);
    setExported(null);
    try {
      const body: Record<string, unknown> = { pages };
      if (url.trim()) body.url = url.trim();
      const res = await fetch(`${API_BASE}/api/crawler/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw await readError(res);
      const data = (await res.json()) as {
        inserted: number;
        skipped: number;
        items: JobItem[];
      };
      setStat({ inserted: data.inserted, skipped: data.skipped });
      setJobs(data.items ?? []);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function exportCsv() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/crawler/export`, {
        method: "POST",
      });
      if (!res.ok) throw await readError(res);
      setExported((await res.json()) as { path: string; rows: number });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      step="⑥"
      title="岗位爬虫面板"
      desc="抓取招聘 JD 并入库；URL 留空即使用默认演示站点"
    >
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="目标 URL（留空 = 默认站）"
          className="w-full max-w-md rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
        />
        <label className="flex items-center gap-2 text-sm text-slate-400">
          页数
          <input
            type="number"
            min={1}
            max={10}
            value={pages}
            onChange={(e) => setPages(Math.max(1, Number(e.target.value) || 1))}
            className="w-20 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
          />
        </label>
        <button
          onClick={run}
          disabled={busy}
          className="rounded-lg bg-violet-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "处理中…" : "开始抓取"}
        </button>
        <button
          onClick={exportCsv}
          disabled={busy}
          className="rounded-lg border border-amber-500/50 bg-amber-500/10 px-5 py-2 text-sm font-medium text-amber-300 transition hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50"
        >
          导出 CSV
        </button>
      </div>

      {error && <ErrorBar message={error} />}

      {stat && (
        <p className="mt-4 text-sm text-slate-400">
          入库 <span className="font-semibold text-emerald-400">{stat.inserted}</span>{" "}
          条，跳过 <span className="font-semibold text-slate-300">{stat.skipped}</span>{" "}
          条（重复）
        </p>
      )}

      {exported && (
        <p className="mt-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-300">
          已导出：<span className="font-mono">{exported.path}</span>（共{" "}
          {exported.rows} 行），Spark 任务可直接读取该文件
        </p>
      )}

      {jobs.length > 0 && (
        <ul className="mt-4 space-y-3">
          {jobs.map((job, i) => (
            <li
              key={i}
              className="rounded-lg border border-slate-800 bg-slate-800/40 px-4 py-3"
            >
              <p className="text-sm font-medium text-slate-200">{job.title}</p>
              <p className="mt-0.5 text-xs text-slate-400">
                {job.company ?? "未知公司"}
                {job.location ? ` · ${job.location}` : ""}
              </p>
              {job.skills?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {job.skills.map((s) => (
                    <Badge key={s} tone="sky">
                      {s}
                    </Badge>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* ---------- 页面主体 ---------- */

export default function Home() {
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [final, setFinal] = useState<FinalResult | MatchFinal | null>(null);
  const [running, setRunning] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false); // 是否正在重试
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const reconnectCountRef = useRef<number>(0); // 已重连次数
  const maxReconnects = 3; // 最多重连 3 次

  useEffect(() => {
    // 未登录直接去登录页（fetch 补丁也会兜底 401 跳转）
    if (!getToken()) window.location.href = "/login";
  }, []);

  useEffect(() => {
    // 页面卸载时关闭 SSE 连接
    return () => esRef.current?.close();
  }, []);

  function subscribe(taskId: string) {
    esRef.current?.close();
    // 清空旧事件：后端会从头回放，避免重连后事件重复
    setEvents([]);
    setFinal(null);
    const es = new EventSource(sseUrl(`/api/tasks/${taskId}/events`));
    esRef.current = es;

    es.onmessage = (ev) => {
      let data: SseEvent;
      try {
        data = JSON.parse(ev.data) as SseEvent;
      } catch {
        return;
      }
      if (data.type === "final") {
        setFinal(data.result as FinalResult | MatchFinal);
        es.close();
        setRunning(false);
        return;
      }
      if (data.type === "error") {
        // 非终态 error（重试中）记录到时间线并显示重试提示，不关闭连接
        setEvents((prev) => [...prev, data]);
        if (data.terminal) {
          es.close();
          setRunning(false);
          setRetrying(false);
          setAskError(String(data.message ?? "任务执行失败"));
        } else {
          // 重试中：显示黄色警告
          setRetrying(true);
          setAskError(String(data.message ?? "执行出错"));
        }
        return;
      }
      // 收到 retry 事件时清除重试状态
      if (data.type === "retry") {
        setRetrying(false);
        setAskError(null);
      }
      setEvents((prev) => [...prev, data]);
    };

    es.addEventListener("done", () => {
      es.close();
      setRunning(false);
    });

    // 断线自动重连（指数退避，最多 3 次）
    es.onerror = () => {
      es.close();
      if (reconnectCountRef.current < maxReconnects) {
        reconnectCountRef.current += 1;
        const delay = Math.min(1000 * 2 ** (reconnectCountRef.current - 1), 8000);
        setOkMsg(`事件流中断，${delay / 1000}s 后重连（第 ${reconnectCountRef.current}/${maxReconnects} 次）…`);
        setTimeout(() => subscribe(taskId), delay);
        return;
      }
      setRunning(false);
      setAskError("事件流连接中断（重连次数已用尽，请刷新页面）");
    };
  }

  async function startTask(query: string) {
    if (!dataset) {
      setAskError("请先上传数据集");
      return;
    }
    setAskError(null);
    setRetrying(false);
    setOkMsg(null);
    setEvents([]);
    setFinal(null);
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_id: dataset.dataset_id, query }),
      });
      if (res.status === 409) {
        const body = (await res.json().catch(() => ({}))) as { task_id?: string };
        if (body.task_id) {
          setOkMsg("已接管进行中任务");
          subscribe(body.task_id);
          return;
        }
        throw await readError(res);
      }
      if (!res.ok) throw await readError(res);
      const { task_id } = (await res.json()) as { task_id: string };
      reconnectCountRef.current = 0;
      subscribe(task_id);
    } catch (e) {
      setAskError(errText(e));
      setRunning(false);
    }
  }

  // 简历匹配：复用与提问相同的 SSE 消费链路（提交时清空旧事件）
  async function startMatch(resumeId: string, jobIds: number[]) {
    setAskError(null);
    setRetrying(false);
    setOkMsg(null);
    setEvents([]);
    setFinal(null);
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/matches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_id: resumeId, job_ids: jobIds }),
      });
      if (res.status === 409) {
        const body = (await res.json().catch(() => ({}))) as { task_id?: string };
        if (body.task_id) {
          setOkMsg("已接管进行中任务");
          subscribe(body.task_id);
          return;
        }
        throw await readError(res);
      }
      if (!res.ok) throw await readError(res);
      const { task_id } = (await res.json()) as { task_id: string };
      reconnectCountRef.current = 0;
      subscribe(task_id);
    } catch (e) {
      setAskError(errText(e));
      setRunning(false);
    }
  }

  return (
    <div className="px-4 py-6">
      {/* Hero：精简为标题 + 副标题 + 两个入口 */}
      <header id="top" className="scroll-mt-20 pb-10">
        <h1 className="text-3xl font-bold tracking-tight text-slate-100 sm:text-4xl">
          Agent
          <span className="bg-gradient-to-r from-sky-400 to-cyan-300 bg-clip-text text-transparent">
            Insight
          </span>
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-slate-400">
          上传 CSV → 用自然语言提问 → 观察 Agent 时间线 →
          拿到图表与结论；也可以上传简历与岗位做匹配分析。后端：
          <span className="font-mono text-slate-500">{API_BASE}</span>
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <a
            href="#analysis"
            className="btn-primary"
          >
            去分析 ↓
          </a>
          <Link
            href="/settings"
            className="btn-ghost"
          >
            去设置
          </Link>
        </div>
      </header>

      <div className="space-y-10">
        {/* 板块一：数据分析工作台（左窄列输入 / 右宽列执行与结果，md 以下单列堆叠） */}
        <Card
          id="analysis"
          icon="📊"
          no="01"
          title="数据分析工作台"
          desc="上传数据集、提交岗位问题，Agent 全过程实时可见，最终产出图表与结论"
        >
          <div className="grid gap-5 md:grid-cols-3">
            <div className="space-y-5 md:col-span-1">
              <UploadPanel dataset={dataset} setDataset={setDataset} />
              <ChatPanel dataset={dataset} running={running} onStartTask={startTask} />
            </div>
            <div className="space-y-5 md:col-span-2">
              {/* 错误展示：终态错误始终显示；重试中显示黄色警告 */}
              {askError && (!running || retrying) && (
                <ErrorBar message={askError} retrying={retrying} />
              )}
              {okMsg && <OkBar message={okMsg} />}
              <TimelinePanel events={events} running={running} />
              {final && final.engine !== "multi_agent" && (
                <ResultCard final={final as FinalResult} />
              )}
              <HistoryPanel
                onReplay={(steps, finalResult) => {
                  // 由历史 trace 合成事件流，复用时间线/结果卡渲染
                  const evs: SseEvent[] = [];
                  (steps as TraceStep[]).forEach((r) => {
                    const sid = (r.detail && r.detail.step_id) || r.agent_name;
                    evs.push({ type: "agent_start", agent: r.agent_name, step: sid } as SseEvent);
                    evs.push({
                      type: "agent_end",
                      agent: r.agent_name,
                      step: sid,
                      status: r.status === "ok" ? "ok" : "error",
                      latency_ms: r.latency_ms ?? 0,
                      detail: r.detail ?? {},
                    } as SseEvent);
                  });
                  setEvents(evs);
                  setFinal(finalResult);
                  setRunning(false);
                  document.getElementById("analysis")?.scrollIntoView({ behavior: "smooth" });
                }}
              />
            </div>
          </div>
        </Card>

        {/* 板块二：简历匹配（结果卡放卡内底部） */}
        <Card
          id="match"
          icon="🧩"
          no="02"
          title="简历匹配"
          desc="上传简历、从岗位库选岗，由多 Agent 打分、找技能缺口并生成解读"
        >
          <div className="space-y-5">
            <MatchPanel running={running} onStartMatch={startMatch} />
            {final && final.engine === "multi_agent" && (
              <MatchResultPanel final={final as MatchFinal} />
            )}
          </div>
        </Card>

        {/* 板块三：岗位爬虫 */}
        <Card
          id="crawler"
          icon="🕸️"
          no="03"
          title="岗位爬虫"
          desc="抓取招聘 JD 并入库，可一键导出 CSV（data/large/jd_crawled.csv）"
        >
          <CrawlerPanel />
        </Card>
      </div>

      <footer className="pb-6 pt-10 text-center text-xs text-slate-600">
        AgentInsight MVP · Next.js 15 + React 19 + Tailwind v4 + ECharts 5
      </footer>
    </div>
  );
}
