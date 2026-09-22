"use client";

/* 区块① 数据集上传：拖拽/点击上传 CSV + 上传进度 + 数据集信息展示 */

import { useCallback, useRef, useState, type DragEvent } from "react";
import { ErrorBar, Badge, Section } from "./ui";
import {
  API_BASE,
  type DatasetInfo,
} from "./shared";
import { ensureAuthToken } from "@/app/auth-client";

/* ---------- 进度条：上传中同时展示文件名与百分比 ---------- */

function ProgressBar({
  percent,
  fileName,
}: {
  percent: number;
  fileName?: string;
}) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs text-[var(--text-secondary)]">
        <span className="min-w-0 truncate" title={fileName}>
          {fileName ? (
            <>
              上传中：<span className="font-mono">{fileName}</span>
            </>
          ) : (
            "上传中…"
          )}
        </span>
        <span className="shrink-0 font-mono">{pct}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--chart-track)]">
        <div
          className="h-full rounded-full bg-[var(--primary)] transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/* ---------- 面板主体 ---------- */

export default function UploadPanel({
  dataset,
  setDataset,
}: {
  dataset: DatasetInfo | null;
  setDataset: (d: DatasetInfo | null) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [schemaOpen, setSchemaOpen] = useState(true);

  const uploadFile = useCallback(
    async (file: File) => {
      if (uploading) return;

      // 非 .csv 明确拒绝（不启动上传、不改动已有 dataset）
      const lower = file.name.toLowerCase();
      if (!lower.endsWith(".csv")) {
        setError(`拒绝上传「${file.name}」：仅支持 .csv 文件`);
        return;
      }

      setUploading(true);
      setProgress(0);
      setError(null);
      setFileName(file.name);

      // ensureAuthToken 失败（返回 null 或抛错）时提示「鉴权未就绪」
      let token: string | null = null;
      try {
        token = await ensureAuthToken();
      } catch {
        token = null;
      }
      if (!token) {
        setError("鉴权未就绪，请稍后重试");
        setUploading(false);
        return;
      }

      const fd = new FormData();
      fd.append("file", file);

      // 使用 XHR 以获得真实上传进度
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/api/datasets`);
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          setProgress(Math.round((e.loaded / e.total) * 100));
        }
      };

      xhr.onload = () => {
        setUploading(false);
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const data = JSON.parse(xhr.responseText) as DatasetInfo;
            if (!Array.isArray(data.schema) || typeof data.rows_estimate !== "number") {
              setError("服务器返回数据不完整");
              return;
            }
            // 协议不变：仍写入 DatasetInfo
            setDataset(data);
            setProgress(100);
            setSchemaOpen(true);
          } catch {
            setError("服务器返回了无效的 JSON");
          }
        } else if (xhr.status === 401) {
          setError("未登录：鉴权未就绪，请再点一次上传");
        } else {
          // 失败文案同时保留 HTTP 状态码与后端 detail
          let detail = "";
          try {
            const body = JSON.parse(xhr.responseText) as { detail?: string };
            if (typeof body.detail === "string" && body.detail) {
              detail = body.detail;
            }
          } catch {
            /* 非 JSON 响应，仅保留 HTTP 状态 */
          }
          setError(
            detail ? `HTTP ${xhr.status}: ${detail}` : `HTTP ${xhr.status}`
          );
        }
      };

      xhr.onerror = () => {
        setUploading(false);
        setError("网络错误，上传失败（HTTP 请求未完成）");
      };

      xhr.send(fd);
    },
    [setDataset, uploading]
  );

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void uploadFile(file);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(true);
  }

  function handleDragLeave() {
    setDragging(false);
  }

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void uploadFile(file);
    e.target.value = "";
  }

  /** 成功/失败后「重新选择」：清错误与进度，打开文件选择；取消则保留现有 dataset */
  function handleReselect() {
    setError(null);
    setProgress(0);
    fileRef.current?.click();
  }

  const sectionDesc = dataset
    ? `拖拽或选择 CSV 文件，系统会自动分析结构与规模 · 已加载 ${fileName || "数据集"}（≈${dataset.rows_estimate.toLocaleString()} 行）`
    : "拖拽或选择 CSV 文件，系统会自动分析结构与规模";

  return (
    <Section
      step="①"
      title="数据集上传"
      desc={sectionDesc}
      collapsible
      defaultOpen={true}
    >
      {/* 拖拽区：input 必须在 button 外，否则无法触发文件选择 */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className={`rounded-lg border border-dashed px-3 py-4 text-center transition ${
          dragging
            ? "border-[var(--primary)] bg-[var(--primary)]/10"
            : "border-[var(--border-default)] hover:border-[var(--primary)]/50 hover:bg-[var(--bg-inset)]"
        }`}
      >
        <div className="mx-auto mb-1 flex h-7 w-7 items-center justify-center rounded-md bg-[var(--primary)]/15 text-sm">
          📄
        </div>
        <div className="text-xs text-[var(--text-secondary)]">
          {dragging ? "松开即可上传" : "拖拽 CSV 到此处，或点击下方选择文件"}
        </div>
        <div className="mt-0.5 text-xs text-[var(--text-muted)]">仅支持 .csv</div>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="btn-primary mt-3 px-4 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
        >
          {dataset || error ? "重新选择 CSV 文件" : "选择 CSV 文件"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          onChange={handleInputChange}
          className="hidden"
        />
      </div>

      {/* 进度：百分比 + 文件名 */}
      {uploading && <ProgressBar percent={progress} fileName={fileName} />}

      {/* 错误：含 HTTP / detail / 鉴权未就绪 / 非 csv 拒绝 */}
      {error && <ErrorBar message={error} />}

      {/* 失败后（尚无 dataset）也提供重新选择入口 */}
      {!uploading && error && !dataset && (
        <div className="mt-3">
          <button
            type="button"
            onClick={handleReselect}
            className="btn-ghost rounded-md border border-[var(--border-default)] px-3 py-1 text-xs text-[var(--text-secondary)]"
          >
            重新选择
          </button>
        </div>
      )}

      {/* 文件名提示 */}
      {fileName && !uploading && !error && (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          已选择：<span className="font-mono">{fileName}</span>
        </p>
      )}

      {/* 数据集信息：表格限高 + 可折叠 +「重新选择」 */}
      {dataset && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Badge tone="sky">dataset_id: {dataset.dataset_id}</Badge>
            <Badge>行数 ≈ {dataset.rows_estimate.toLocaleString()}</Badge>
            <Badge>{dataset.size_mb.toFixed(2)} MB</Badge>
            <Badge tone="violet">引擎: {dataset.engine_hint}</Badge>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setSchemaOpen((v) => !v)}
              className="btn-ghost rounded-md border border-[var(--border-default)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)]"
              aria-expanded={schemaOpen}
            >
              {schemaOpen ? "收起字段列表" : "展开字段列表"}
            </button>
            <button
              type="button"
              onClick={handleReselect}
              className="btn-ghost rounded-md border border-[var(--border-default)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)]"
            >
              重新选择
            </button>
          </div>

          {schemaOpen && (
            <div className="max-h-40 overflow-auto rounded-md border border-[var(--border-default)]">
              <table className="w-full text-left text-xs">
                <thead className="bg-[var(--bg-header)] text-[var(--text-secondary)]">
                  <tr>
                    <th className="px-2 py-1 font-medium">列名</th>
                    <th className="px-2 py-1 font-medium">类型</th>
                  </tr>
                </thead>
                <tbody>
                  {dataset.schema.map((col) => (
                    <tr
                      key={col.name}
                      className="border-t border-[var(--border-default)] text-[var(--text-secondary)]"
                    >
                      <td className="px-2 py-1 font-mono">{col.name}</td>
                      <td className="px-2 py-1 text-[var(--text-muted)]">
                        {col.type}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
