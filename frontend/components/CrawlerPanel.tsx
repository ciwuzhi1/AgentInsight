"use client";

/* 区块⑥ 爬虫面板 */

import { useState } from "react";
import { ErrorBar, Badge, Section } from "./ui";
import { API_BASE, errText, readError, type JobItem } from "./shared";

export default function CrawlerPanel() {
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
          className="w-full max-w-md rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-4 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--primary)] focus:outline-none"
        />
        <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
          页数
          <input
            type="number"
            min={1}
            max={10}
            value={pages}
            onChange={(e) => setPages(Math.max(1, Number(e.target.value) || 1))}
            className="w-20 rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--primary)] focus:outline-none"
          />
        </label>
        <button
          onClick={run}
          disabled={busy}
          className="btn-primary rounded-lg bg-[var(--accent-violet)] px-5 py-2 text-sm font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "处理中…" : "开始抓取"}
        </button>
        <button
          onClick={exportCsv}
          disabled={busy}
          className="btn-ghost rounded-lg border border-[var(--status-running-border)] bg-[var(--status-running-bg)] px-5 py-2 text-sm font-medium text-[var(--status-running)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          导出 CSV
        </button>
      </div>

      {error && <ErrorBar message={error} />}

      {stat && (
        <p className="mt-4 text-sm text-[var(--text-secondary)]">
          入库{" "}
          <span className="font-semibold text-[var(--status-ok)]">
            {stat.inserted}
          </span>{" "}
          条，跳过{" "}
          <span className="font-semibold text-[var(--text-primary)]">
            {stat.skipped}
          </span>{" "}
          条（重复）
        </p>
      )}

      {exported && (
        <p className="mt-2 rounded-lg border border-[var(--status-ok-border)] bg-[var(--status-ok-bg)] px-4 py-2 text-sm text-[var(--status-ok)]">
          已导出：
          <span className="font-mono">{exported.path}</span>（共{" "}
          {exported.rows} 行），Spark 任务可直接读取该文件
        </p>
      )}

      {jobs.length > 0 && (
        <ul className="mt-4 space-y-3">
          {jobs.map((job, i) => (
            <li
              key={i}
              className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-4 py-3"
            >
              <p className="text-sm font-medium text-[var(--text-primary)]">
                {job.title}
              </p>
              <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
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
