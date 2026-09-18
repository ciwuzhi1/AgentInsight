"use client";

/* 区块① 数据集上传：拖拽/点击上传 CSV + 上传进度 + 数据集信息展示 */

import { useCallback, useRef, useState, type DragEvent } from "react";
import { ErrorBar, Badge, Section } from "./ui";
import {
  API_BASE,
  type DatasetInfo,
} from "./shared";
import { ensureAuthToken } from "@/app/auth-client";

/* ---------- 进度条 ---------- */

function ProgressBar({ percent }: { percent: number }) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between text-xs text-[var(--text-secondary)]">
        <span>上传中…</span>
        <span className="font-mono">{pct}%</span>
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

  const uploadFile = useCallback(
    async (file: File) => {
      if (uploading) return;
      if (!file.name.toLowerCase().endsWith(".csv")) {
        setError("仅支持 .csv 文件");
        return;
      }
      setUploading(true);
      setProgress(0);
      setError(null);
      setFileName(file.name);

      const token = await ensureAuthToken();
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
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

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
            setDataset(data);
            setProgress(100);
          } catch {
            setError("服务器返回了无效的 JSON");
          }
        } else if (xhr.status === 401) {
          setError("未登录：正在自动重连，请再点一次上传");
        } else {
          let msg = `HTTP ${xhr.status}`;
          try {
            const body = JSON.parse(xhr.responseText) as { detail?: string };
            if (body.detail) msg = body.detail;
          } catch {
            /* keep default */
          }
          setError(msg);
        }
      };

      xhr.onerror = () => {
        setUploading(false);
        setError("网络错误，上传失败");
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
          className="btn-primary mt-3 px-4 py-1.5 text-xs"
        >
          选择 CSV 文件
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          onChange={handleInputChange}
          className="hidden"
        />
      </div>

      {/* 进度 */}
      {uploading && <ProgressBar percent={progress} />}

      {/* 错误 */}
      {error && <ErrorBar message={error} />}

      {/* 文件名提示 */}
      {fileName && !uploading && !error && (
        <p className="mt-3 text-xs text-[var(--text-muted)]">
          已选择：<span className="font-mono">{fileName}</span>
        </p>
      )}

      {/* 数据集信息 */}
      {dataset && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Badge tone="sky">dataset_id: {dataset.dataset_id}</Badge>
            <Badge>行数 ≈ {dataset.rows_estimate.toLocaleString()}</Badge>
            <Badge>{dataset.size_mb.toFixed(2)} MB</Badge>
            <Badge tone="violet">引擎: {dataset.engine_hint}</Badge>
          </div>
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
        </div>
      )}
    </Section>
  );
}
