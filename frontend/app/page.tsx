"use client";

import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

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

type ChartSpec = { type: string; x_field: string; y_fields: string[] };

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

type JobItem = {
  title: string;
  company: string | null;
  location: string | null;
  skills: string[];
};

/* ---------- 小组件 ---------- */

function ErrorBar({ message }: { message: string }) {
  return (
    <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
      出错了：{message}
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
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(
          (detail as { detail?: string }).detail ?? `HTTP ${res.status}`
        );
      }
      setDataset((await res.json()) as DatasetInfo);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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

/* ---------- 区块③ Agent 执行时间线 ---------- */

function buildTimeline(events: SseEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  for (const ev of events) {
    switch (ev.type) {
      case "agent_start":
        items.push({
          kind: "agent",
          agent: String(ev.agent ?? ""),
          status: "running",
        });
        break;
      case "agent_end": {
        const target = [...items]
          .reverse()
          .find(
            (i) =>
              i.kind === "agent" &&
              i.agent === ev.agent &&
              i.status === "running"
          );
        if (target) {
          target.status = String(ev.status ?? "ok");
          target.latency_ms = Number(ev.latency_ms ?? 0);
        } else {
          items.push({
            kind: "agent",
            agent: String(ev.agent ?? ""),
            status: String(ev.status ?? "ok"),
            latency_ms: Number(ev.latency_ms ?? 0),
          });
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
  return items;
}

function TimelinePanel({
  events,
  running,
}: {
  events: SseEvent[];
  running: boolean;
}) {
  const items = buildTimeline(events);
  if (items.length === 0 && !running) return null;

  return (
    <Section
      step="③"
      title="Agent 执行时间线"
      desc="Agent 的每一步都会实时推送到这里"
    >
      {running && items.length === 0 && (
        <p className="animate-pulse text-sm text-slate-400">
          正在等待 Agent 开始执行…
        </p>
      )}
      <ol className="space-y-3">
        {items.map((it, idx) => {
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
          // agent
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

/* ---------- 区块⑤ 爬虫面板 ---------- */

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
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(
          (detail as { detail?: string }).detail ?? `HTTP ${res.status}`
        );
      }
      const data = (await res.json()) as {
        inserted: number;
        skipped: number;
        items: JobItem[];
      };
      setStat({ inserted: data.inserted, skipped: data.skipped });
      setJobs(data.items ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(
          (detail as { detail?: string }).detail ?? `HTTP ${res.status}`
        );
      }
      setExported((await res.json()) as { path: string; rows: number });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      step="⑤"
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

/* ---------- 页面主体 ---------- */

export default function Home() {
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [final, setFinal] = useState<FinalResult | null>(null);
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
        setFinal(data.result as FinalResult);
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
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(
          (detail as { detail?: string }).detail ?? `HTTP ${res.status}`
        );
      }
      const { task_id } = (await res.json()) as { task_id: string };
      subscribe(task_id);
    } catch (e) {
      setAskError(e instanceof Error ? e.message : String(e));
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
          上传 CSV → 用自然语言提问 → 观察 Agent 时间线 → 拿到图表与结论。
          后端：<span className="font-mono text-slate-500">{API_BASE}</span>
        </p>
      </header>

      <UploadPanel dataset={dataset} setDataset={setDataset} />

      <div className="border-t border-slate-800/60" />
      <ChatPanel dataset={dataset} running={running} onStartTask={startTask} />

      {askError && !running && <ErrorBar message={askError} />}
      <TimelinePanel events={events} running={running} />
      {final && <ResultPanel final={final} />}

      <div className="border-t border-slate-800/60" />
      <CrawlerPanel />

      <footer className="pb-6 pt-2 text-center text-xs text-slate-600">
        AgentInsight MVP · Next.js 15 + React 19 + Tailwind v4 + ECharts 5
      </footer>
    </main>
  );
}
