"use client";

import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

const SAMPLE_QUESTIONS = [
  "按地区统计总销售额",
  "销量 Top 5 的商品",
  "按月份统计销售额趋势",
  "哪个品类销售额最高",
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

type TimelineItem = {
  kind: "agent" | "engine" | "sql" | "error" | "state";
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
};

/* plan 事件里的一个步骤声明 */
type PlanStepInfo = { id: string; agent: string; depends_on: string[] };

/* 基于 plan 构建的步骤卡（agent_start/end 按 step id 配对） */
type StepCard = {
  step_id: string;
  agent: string;
  depends_on: string[];
  status: "pending" | "running" | "ok" | "error" | "skipped";
  statusText?: string;
  latency_ms?: number;
  retries: number;
};

type TimelineModel = {
  hasPlan: boolean;
  steps: StepCard[];
  items: TimelineItem[];
};

type ChartSpec = { type: string; x_field: string; y_fields: string[] };

/* 数据形态 final（CONTRACTS.md §6） */
type FinalResult = {
  task_id: string;
  query: string;
  engine: string;
  sql: string | null;
  explanation: string;
  columns: string[];
  rows: (string | number | null)[][];
  row_count: number;
  truncated: boolean;
  chart: ChartSpec | null;
  elapsed_ms: number;
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

type ModelConfig = {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  is_active: boolean;
};

type SettingMeta = {
  key: string;
  label: string;
  kind: "select" | "text" | "number" | "secret";
  options?: string[];
};

const SETTING_META: SettingMeta[] = [
  { key: "match_llm_enabled", label: "匹配 LLM 解读", kind: "select", options: ["true", "false"] },
  { key: "llm_fallback_mock", label: "LLM 兜底策略", kind: "select", options: ["auto", "never"] },
  { key: "parser_backend", label: "PDF 解析后端", kind: "select", options: ["mineru_api", "pymupdf"] },
  { key: "sql_timeout", label: "SQL 超时（秒）", kind: "number" },
  { key: "mineru_api_token", label: "MinerU Token", kind: "secret" },
  { key: "tavily_api_key", label: "Tavily API Key", kind: "secret" },
];

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

function ErrorBar({ message }: { message: string }) {
  return (
    <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
      出错了：{message}
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
    <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 shadow-lg shadow-black/20">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-slate-100">
          <span className="mr-2 inline-block rounded-md bg-sky-500/15 px-2 py-0.5 text-sm text-sky-400">
            {step}
          </span>
          {title}
        </h2>
        {desc && <p className="mt-1 text-sm text-slate-400">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

/* ---------- 图表：ECharts 由 React 驱动 ---------- */

function ChartBox({
  chart,
  columns,
  rows,
}: {
  chart: ChartSpec;
  columns: string[];
  rows: (string | number | null)[][];
}) {
  const divRef = useRef<HTMLDivElement>(null);
  const instRef = useRef<echarts.ECharts | null>(null);

  // 挂载时 init 一次，卸载时 dispose
  useEffect(() => {
    if (!divRef.current) return;
    const inst = echarts.init(divRef.current, "dark");
    instRef.current = inst;
    const onResize = () => inst.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst.dispose();
      instRef.current = null;
    };
  }, []);

  // 数据变化时把 option 塞给图表实例
  useEffect(() => {
    const inst = instRef.current;
    if (!inst) return;
    const xIdx = columns.indexOf(chart.x_field);
    const series = chart.y_fields.map((yf) => {
      const yIdx = columns.indexOf(yf);
      return {
        name: yf,
        type: chart.type === "line" ? ("line" as const) : ("bar" as const),
        data: rows.map((r) => {
          const v = yIdx >= 0 ? r[yIdx] : null;
          const n = typeof v === "number" ? v : Number(v);
          return Number.isFinite(n) ? n : 0;
        }),
      };
    });
    inst.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: chart.y_fields.length > 1 ? {} : undefined,
      grid: { left: 48, right: 24, top: 36, bottom: 48 },
      xAxis: {
        type: "category",
        data: rows.map((r) => (xIdx >= 0 ? String(r[xIdx] ?? "") : "")),
        axisLabel: { color: "#94a3b8" },
      },
      yAxis: { type: "value", axisLabel: { color: "#94a3b8" } },
      series,
    });
  }, [chart, columns, rows]);

  return <div ref={divRef} className="h-80 w-full" />;
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

  function submit(q?: string) {
    const text = (q ?? query).trim();
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
      title="用一句话提问"
      desc="Agent 会把你的问题翻译成 SQL 并在引擎上执行，全过程实时可见"
    >
      <textarea
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        rows={2}
        placeholder={
          dataset
            ? "例如：按地区统计总销售额"
            : "请先上传数据集，再在这里输入问题…"
        }
        className="w-full resize-none rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
      />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => submit()}
          disabled={running}
          className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "Agent 分析中…" : "提交问题"}
        </button>
        {SAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            onClick={() => submit(q)}
            disabled={running || !dataset}
            className="rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-xs text-slate-300 transition hover:border-sky-500/60 hover:text-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {q}
          </button>
        ))}
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
      default:
        break;
    }
  }
  return { hasPlan, steps, items };
}

function StepCardView({ card }: { card: StepCard }) {
  const borderTone =
    card.status === "running"
      ? "border-amber-500/40"
      : card.status === "ok"
        ? "border-emerald-500/30"
        : card.status === "error"
          ? "border-red-500/40"
          : "border-slate-800";
  return (
    <div className={`rounded-lg border ${borderTone} bg-slate-800/40 px-4 py-3`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-300">
          🤖 <span className="font-mono">{card.agent}</span>
        </span>
        {card.status === "pending" && <Badge>待执行</Badge>}
        {card.status === "running" && <Badge tone="amber">运行中…</Badge>}
        {card.status === "ok" && <Badge tone="green">完成</Badge>}
        {card.status === "error" && (
          <Badge tone="red">{card.statusText ?? "失败"}</Badge>
        )}
        {card.status === "skipped" && <Badge tone="slate">已跳过</Badge>}
        {card.retries > 0 && <Badge tone="amber">重试 ×{card.retries}</Badge>}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span className="font-mono">step: {card.step_id}</span>
        {card.depends_on.length > 0 && (
          <span>依赖: {card.depends_on.join("、")}</span>
        )}
        {card.latency_ms !== undefined && <span>{card.latency_ms} ms</span>}
      </div>
    </div>
  );
}

function TimelinePanel({
  events,
  running,
}: {
  events: SseEvent[];
  running: boolean;
}) {
  const model = buildTimeline(events);
  const empty =
    model.steps.length === 0 && model.items.length === 0;
  if (empty && !running) return null;

  // depends_on 相同的相邻步骤是并行关系 → 同一波次，渲染为多列
  const waves: StepCard[][] = [];
  for (const card of model.steps) {
    const last = waves[waves.length - 1];
    const key = card.depends_on.join(",");
    if (last && last[0].depends_on.join(",") === key) {
      last.push(card);
    } else {
      waves.push([card]);
    }
  }

  return (
    <Section
      step="③"
      title="Agent 执行时间线"
      desc="Agent 的每一步都会实时推送到这里；依赖相同的步骤并行执行"
    >
      {running && empty && (
        <p className="animate-pulse text-sm text-slate-400">
          正在等待 Agent 开始执行…
        </p>
      )}

      {model.hasPlan && model.steps.length > 0 && (
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

      {model.items.length > 0 && (
        <ol className={`space-y-3 ${model.hasPlan && model.steps.length > 0 ? "mt-4 border-t border-slate-800/60 pt-4" : ""}`}>
          {model.items.map((it, idx) => {
            if (it.kind === "state") {
              return (
                <li key={idx} className="text-xs text-slate-500">
                  状态切换 → {it.state_status}
                </li>
              );
            }
            if (it.kind === "engine") {
              return (
                <li
                  key={idx}
                  className="flex flex-wrap items-center gap-2 text-sm"
                >
                  <Badge tone="violet">引擎: {it.engine}</Badge>
                  <span className="text-slate-400">
                    预估行数 {it.rows_estimate?.toLocaleString()}
                    {it.reason ? `（${it.reason}）` : ""}
                  </span>
                </li>
              );
            }
            if (it.kind === "sql") {
              return (
                <li key={idx}>
                  <p className="mb-1 text-xs text-slate-400">
                    {it.explanation ?? "生成的 SQL"}
                  </p>
                  <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 px-4 py-3 font-mono text-xs text-emerald-300">
                    {it.sql}
                  </pre>
                </li>
              );
            }
            if (it.kind === "error") {
              return (
                <li
                  key={idx}
                  className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300"
                >
                  <Badge tone="red">{it.code}</Badge>
                  <span className="ml-2">{it.message}</span>
                </li>
              );
            }
            // agent（旧链路平铺）
            const ok = it.status === "ok";
            return (
              <li
                key={idx}
                className="flex flex-wrap items-center gap-2 text-sm"
              >
                <span className="text-slate-300">
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
                  <span className="text-xs text-slate-500">
                    {it.latency_ms} ms
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}

/* ---------- 区块④ 结果卡片 ---------- */

function ResultPanel({ final }: { final: FinalResult }) {
  const hasChart = final.chart && final.columns.length > 0 && final.rows.length > 0;
  const tableRows = final.rows.slice(0, 50);

  return (
    <Section step="④" title="分析结果" desc="说明、图表与数据表">
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="sky">引擎: {final.engine}</Badge>
          <Badge>耗时 {final.elapsed_ms} ms</Badge>
          <Badge>
            {final.row_count} 行{final.truncated ? "（已截断）" : ""}
          </Badge>
        </div>

        {final.explanation && (
          <p className="rounded-lg border border-slate-800 bg-slate-800/40 px-4 py-3 text-sm leading-relaxed text-slate-200">
            {final.explanation}
          </p>
        )}

        {final.sql && (
          <details className="group rounded-lg border border-slate-800">
            <summary className="cursor-pointer px-4 py-2 text-sm text-slate-400 transition hover:text-slate-200">
              查看 SQL（点击展开/收起）
            </summary>
            <pre className="overflow-x-auto border-t border-slate-800 bg-slate-950 px-4 py-3 font-mono text-xs text-emerald-300">
              {final.sql}
            </pre>
          </details>
        )}

        {hasChart && final.chart && (
          <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
            <ChartBox
              chart={final.chart}
              columns={final.columns}
              rows={final.rows}
            />
          </div>
        )}

        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-800/80 text-slate-300">
              <tr>
                {final.columns.map((c) => (
                  <th key={c} className="px-4 py-2 font-medium whitespace-nowrap">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row, i) => (
                <tr key={i} className="border-t border-slate-800 text-slate-300">
                  {row.map((cell, j) => (
                    <td key={j} className="px-4 py-1.5 whitespace-nowrap">
                      {cell === null ? "-" : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {final.rows.length > 50 && (
            <p className="border-t border-slate-800 px-4 py-2 text-xs text-slate-500">
              仅显示前 50 行，共 {final.row_count} 行
            </p>
          )}
        </div>
      </div>
    </Section>
  );
}

/* ---------- 匹配结果卡（engine === "multi_agent"） ---------- */

function scoreColor(score: number): string {
  if (score >= 80) return "#34d399";
  if (score >= 60) return "#38bdf8";
  return "#fbbf24";
}

function ScoreRing({ score }: { score: number }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  return (
    <svg viewBox="0 0 120 120" className="h-32 w-32">
      <circle cx="60" cy="60" r={r} fill="none" stroke="#1e293b" strokeWidth="10" />
      <circle
        cx="60"
        cy="60"
        r={r}
        fill="none"
        stroke={scoreColor(score)}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${c * pct} ${c}`}
        transform="rotate(-90 60 60)"
      />
      <text
        x="60"
        y="70"
        textAnchor="middle"
        fontSize="30"
        fontWeight="bold"
        fill={scoreColor(score)}
      >
        {score}
      </text>
    </svg>
  );
}

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
            <p className="text-sm text-slate-500">
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
          导出 CSV 给 Spark
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

/* ---------- 区块⑦ 设置中心 ---------- */

function SettingsPanel() {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [form, setForm] = useState({
    name: "",
    provider: "openai_compatible",
    base_url: "",
    api_key: "",
    model: "",
    temperature: "0.3",
  });

  async function loadAll() {
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const [mRes, sRes] = await Promise.all([
        fetch(`${API_BASE}/api/models`),
        fetch(`${API_BASE}/api/settings`),
      ]);
      if (!mRes.ok) throw new Error(`加载模型列表失败（HTTP ${mRes.status}）`);
      if (!sRes.ok) throw new Error(`加载设置失败（HTTP ${sRes.status}）`);
      const m = (await mRes.json()) as unknown;
      const s = (await sRes.json()) as Record<string, string>;
      const list = Array.isArray(m)
        ? m
        : ((m as { items?: ModelConfig[] }).items ?? []);
      setModels(list as ModelConfig[]);
      setSettings(s ?? {});
      setDraft({});
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  function resetForm() {
    setForm({
      name: "",
      provider: "openai_compatible",
      base_url: "",
      api_key: "",
      model: "",
      temperature: "0.3",
    });
  }

  async function addModel() {
    if (
      !form.name.trim() ||
      !form.base_url.trim() ||
      !form.api_key.trim() ||
      !form.model.trim()
    ) {
      setError("请填写完整的模型配置（名称 / Base URL / API Key / 模型名）");
      return;
    }
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name.trim(),
          provider: form.provider,
          base_url: form.base_url.trim(),
          api_key: form.api_key.trim(),
          model: form.model.trim(),
          temperature: Number(form.temperature) || 0,
        }),
      });
      if (!res.ok) throw await readError(res);
      resetForm();
      setOkMsg("模型配置已添加");
      await loadAll();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function activateModel(id: string) {
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/models/${id}/activate`, {
        method: "PUT",
      });
      if (!res.ok) throw await readError(res);
      setOkMsg("已切换激活模型");
      await loadAll();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function testModel(id: string) {
    setTestingId(id);
    setError(null);
    setTestResult((prev) => ({ ...prev, [id]: "测试中…" }));
    try {
      const res = await fetch(`${API_BASE}/api/models/${id}/test`, {
        method: "POST",
      });
      if (!res.ok) throw await readError(res);
      const data = (await res.json()) as {
        ok: boolean;
        latency_ms?: number;
        error?: string;
      };
      setTestResult((prev) => ({
        ...prev,
        [id]: data.ok
          ? `连通正常 · ${data.latency_ms ?? "?"} ms`
          : `失败：${data.error ?? "未知错误"}`,
      }));
    } catch (e) {
      setTestResult((prev) => ({ ...prev, [id]: `失败：${errText(e)}` }));
    } finally {
      setTestingId(null);
    }
  }

  async function deleteModel(id: string) {
    if (!window.confirm("确定删除该模型配置吗？")) return;
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/models/${id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw await readError(res);
      setOkMsg("模型配置已删除");
      await loadAll();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveSetting(meta: SettingMeta) {
    setError(null);
    setOkMsg(null);
    const typed = draft[meta.key];
    if (meta.kind === "secret" && !typed) {
      setError(`请先输入「${meta.label}」的新值（已配置的密钥不会回显明文）`);
      return;
    }
    const value = typed ?? settings[meta.key] ?? "";
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: meta.key, value }),
      });
      if (!res.ok) throw await readError(res);
      setOkMsg(`已保存「${meta.label}」`);
      setDraft((prev) => ({ ...prev, [meta.key]: "" }));
      await loadAll();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none";

  return (
    <details
      className="group rounded-2xl border border-slate-800 bg-slate-900/60 shadow-lg shadow-black/20"
      onToggle={(e) => {
        if ((e.target as HTMLDetailsElement).open) loadAll();
      }}
    >
      <summary className="cursor-pointer px-6 py-5">
        <h2 className="text-lg font-semibold text-slate-100">
          <span className="mr-2 inline-block rounded-md bg-sky-500/15 px-2 py-0.5 text-sm text-sky-400">
            ⑦
          </span>
          设置中心
          <span className="ml-3 text-sm font-normal text-slate-500">
            （点击展开：模型配置与开关，展开时自动加载）
          </span>
        </h2>
      </summary>

      <div className="space-y-6 px-6 pb-6">
        {error && <ErrorBar message={error} />}
        {okMsg && !error && <OkBar message={okMsg} />}

        {/* 模型列表 */}
        <div>
          <h3 className="mb-3 text-sm font-semibold text-slate-300">模型配置</h3>
          {models.length === 0 ? (
            <p className="text-sm text-slate-500">
              暂无模型配置（当前使用后端 .env 的默认模型）
            </p>
          ) : (
            <ul className="space-y-3">
              {models.map((m) => (
                <li
                  key={m.id}
                  className="rounded-lg border border-slate-800 bg-slate-800/40 px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-slate-200">
                      {m.name}
                    </span>
                    <Badge tone="sky">{m.provider}</Badge>
                    <Badge tone="violet">{m.model}</Badge>
                    {m.is_active ? (
                      <Badge tone="green">激活中</Badge>
                    ) : (
                      <Badge>未激活</Badge>
                    )}
                    <Badge tone="slate">key: {m.api_key}</Badge>
                  </div>
                  <p className="mt-1 font-mono text-xs text-slate-500">
                    {m.base_url} · temperature {m.temperature}
                  </p>
                  {testResult[m.id] && (
                    <p
                      className={`mt-1 text-xs ${
                        testResult[m.id].startsWith("连通")
                          ? "text-emerald-400"
                          : testResult[m.id] === "测试中…"
                            ? "text-slate-400"
                            : "text-red-400"
                      }`}
                    >
                      测试结果：{testResult[m.id]}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      onClick={() => activateModel(m.id)}
                      disabled={busy || m.is_active}
                      className="rounded-lg border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      激活
                    </button>
                    <button
                      onClick={() => testModel(m.id)}
                      disabled={testingId === m.id}
                      className="rounded-lg border border-sky-500/50 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 transition hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {testingId === m.id ? "测试中…" : "测试连通"}
                    </button>
                    <button
                      onClick={() => deleteModel(m.id)}
                      disabled={busy}
                      className="rounded-lg border border-red-500/50 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      删除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 新增模型表单 */}
        <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-300">
            新增模型（OpenAI 兼容接口）
          </h3>
          <div className="grid gap-3 md:grid-cols-2">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="配置名称，如：DeepSeek 主力"
              className={inputCls}
            />
            <select
              value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
              className={inputCls}
            >
              <option value="openai_compatible">openai_compatible</option>
              <option value="openai">openai</option>
              <option value="deepseek">deepseek</option>
              <option value="qwen">qwen</option>
              <option value="moonshot">moonshot</option>
            </select>
            <input
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              placeholder="Base URL，如：https://api.deepseek.com/v1"
              className={inputCls}
            />
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="API Key"
              className={inputCls}
            />
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              placeholder="模型名，如：deepseek-chat"
              className={inputCls}
            />
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={form.temperature}
              onChange={(e) => setForm({ ...form, temperature: e.target.value })}
              placeholder="temperature"
              className={inputCls}
            />
          </div>
          <button
            onClick={addModel}
            disabled={busy}
            className="mt-3 rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            添加模型
          </button>
        </div>

        {/* 开关区 */}
        <div className="rounded-lg border border-slate-800 bg-slate-800/40 p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-300">开关与密钥</h3>
          <ul className="space-y-3">
            {SETTING_META.map((meta) => (
              <li
                key={meta.key}
                className="flex flex-wrap items-center gap-3 text-sm"
              >
                <span className="w-40 shrink-0 text-slate-300">{meta.label}</span>
                <span className="font-mono text-xs text-slate-600">
                  {meta.key}
                </span>
                {meta.kind === "select" ? (
                  <select
                    value={draft[meta.key] ?? settings[meta.key] ?? ""}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, [meta.key]: e.target.value }))
                    }
                    className={inputCls}
                  >
                    {meta.options?.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={meta.kind === "secret" ? "password" : meta.kind === "number" ? "number" : "text"}
                    value={draft[meta.key] ?? ""}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, [meta.key]: e.target.value }))
                    }
                    placeholder={
                      meta.kind === "secret"
                        ? settings[meta.key]
                          ? "已配置（输入新值覆盖）"
                          : "未配置"
                        : settings[meta.key] || ""
                    }
                    className={`${inputCls} w-56`}
                  />
                )}
                <button
                  onClick={() => saveSetting(meta)}
                  disabled={busy}
                  className="rounded-lg border border-sky-500/50 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 transition hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  保存
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-slate-500">
            密钥类输入框留空表示不修改；保存后立即生效（后端有 10 秒缓存）。
          </p>
        </div>
      </div>
    </details>
  );
}

/* ---------- 页面主体 ---------- */

export default function Home() {
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [final, setFinal] = useState<FinalResult | MatchFinal | null>(null);
  const [running, setRunning] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // 页面卸载时关闭 SSE 连接
    return () => esRef.current?.close();
  }, []);

  function subscribe(taskId: string) {
    esRef.current?.close();
    const es = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
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
        es.close();
        setRunning(false);
        setEvents((prev) => [...prev, data]);
        setAskError(String(data.message ?? "任务执行失败"));
        return;
      }
      setEvents((prev) => [...prev, data]);
    };

    es.addEventListener("done", () => {
      es.close();
      setRunning(false);
    });

    es.onerror = () => {
      es.close();
      setRunning(false);
      setAskError("事件流连接中断（后端可能未启动或已断开）");
    };
  }

  async function startTask(query: string) {
    if (!dataset) {
      setAskError("请先上传数据集");
      return;
    }
    setAskError(null);
    setEvents([]);
    setFinal(null);
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_id: dataset.dataset_id, query }),
      });
      if (!res.ok) throw await readError(res);
      const { task_id } = (await res.json()) as { task_id: string };
      subscribe(task_id);
    } catch (e) {
      setAskError(errText(e));
      setRunning(false);
    }
  }

  // 简历匹配：复用与提问相同的 SSE 消费链路（提交时清空旧事件）
  async function startMatch(resumeId: string, jobIds: number[]) {
    setAskError(null);
    setEvents([]);
    setFinal(null);
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/matches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_id: resumeId, job_ids: jobIds }),
      });
      if (!res.ok) throw await readError(res);
      const { task_id } = (await res.json()) as { task_id: string };
      subscribe(task_id);
    } catch (e) {
      setAskError(errText(e));
      setRunning(false);
    }
  }

  return (
    <main className="space-y-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight text-slate-100">
          Agent<span className="text-sky-400">Insight</span>
        </h1>
        <p className="mt-2 text-sm text-slate-400">
          上传 CSV → 用自然语言提问 → 观察 Agent 时间线 → 拿到图表与结论；
          也可以上传简历与岗位做匹配分析。后端：
          <span className="font-mono text-slate-500">{API_BASE}</span>
        </p>
      </header>

      <UploadPanel dataset={dataset} setDataset={setDataset} />

      <div className="border-t border-slate-800/60" />
      <ChatPanel dataset={dataset} running={running} onStartTask={startTask} />

      {askError && !running && <ErrorBar message={askError} />}
      <TimelinePanel events={events} running={running} />
      {final &&
        (final.engine === "multi_agent" ? (
          <MatchResultPanel final={final as MatchFinal} />
        ) : (
          <ResultPanel final={final as FinalResult} />
        ))}

      <div className="border-t border-slate-800/60" />
      <MatchPanel running={running} onStartMatch={startMatch} />

      <div className="border-t border-slate-800/60" />
      <CrawlerPanel />

      <div className="border-t border-slate-800/60" />
      <SettingsPanel />

      <footer className="pb-6 pt-2 text-center text-xs text-slate-600">
        AgentInsight MVP · Next.js 15 + React 19 + Tailwind v4 + ECharts 5
      </footer>
    </main>
  );
}
