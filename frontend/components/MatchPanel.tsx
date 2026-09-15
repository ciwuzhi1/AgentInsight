"use client";

/* 区块⑤ 简历匹配 */

import { useEffect, useRef, useState } from "react";
import { ErrorBar, OkBar, Badge, Section } from "./ui";
import {
  API_BASE,
  errText,
  readError,
  type CrawlerJobItem,
} from "./shared";

export default function MatchPanel({
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
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] p-4">
          <p className="mb-3 text-sm font-medium text-[var(--text-primary)]">
            上传简历
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt"
              onChange={(e) => setFileName(e.target.files?.[0]?.name ?? "")}
              className="block w-full cursor-pointer rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-3 py-2 text-sm text-[var(--text-secondary)] file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-[var(--primary)] file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white hover:file:bg-[var(--primary-light)]"
            />
            <button
              onClick={uploadResume}
              disabled={uploading}
              className="btn-primary rounded-lg px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {uploading ? "上传中…" : "上传简历"}
            </button>
          </div>
          {fileName && (
            <p className="mt-2 text-xs text-[var(--text-muted)]">
              已选择：{fileName}
            </p>
          )}
          {resume && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="sky">resume_id: {resume.resume_id}</Badge>
              <Badge tone="green">{resume.filename}</Badge>
            </div>
          )}
          <p className="mt-3 text-xs text-[var(--text-muted)]">
            支持 pdf / docx / txt，最大 10MB；再次上传会覆盖之前的简历。
          </p>
        </div>

        {/* 右半：选择岗位 */}
        <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-[var(--text-primary)]">
              选择岗位（最多 5 个，已选 {selected.length}）
            </p>
            {selected.length > 0 && (
              <button
                onClick={() => setSelected([])}
                className="text-xs text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
              >
                清空
              </button>
            )}
          </div>
          {jobsLoading && (
            <p className="animate-pulse text-sm text-[var(--text-secondary)]">
              正在加载岗位库…
            </p>
          )}
          {jobsError && <ErrorBar message={jobsError} />}
          {!jobsLoading && !jobsError && jobs.length === 0 && (
            <p className="rounded-lg border border-dashed border-[var(--border-default)] py-6 text-center text-sm text-[var(--text-muted)]">
              岗位库还是空的：请先在下方爬虫面板抓取岗位，然后刷新本页。
            </p>
          )}
          {jobs.length > 0 && (
            <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
              {jobs.map((job) => (
                <li key={job.id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 text-sm transition hover:border-[var(--border-default)] hover:bg-[var(--bg-inset)]">
                    <input
                      type="checkbox"
                      checked={selected.includes(job.id)}
                      onChange={() => toggleJob(job.id)}
                      className="mt-0.5 h-4 w-4 accent-[var(--primary)]"
                    />
                    <span>
                      <span className="text-[var(--text-primary)]">
                        {job.title}
                      </span>
                      <span className="text-[var(--text-muted)]">
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
          className="btn-primary rounded-lg px-5 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {running ? "匹配进行中…" : "开始匹配"}
        </button>
        <span className="ml-3 text-xs text-[var(--text-muted)]">
          匹配过程与结果复用上方的执行时间线和结果区
        </span>
      </div>
    </Section>
  );
}
