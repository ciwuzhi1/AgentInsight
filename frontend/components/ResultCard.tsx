"use client";

import ChartBox, { type ChartSpec } from "./ChartBox";

/* ---------- 类型（与 app/page.tsx 对齐） ---------- */

export type FinalResult = {
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

export type { ChartSpec };

/* ---------- 子组件 ---------- */

/** SQL 展示块：可折叠 details + 等宽代码高亮底色 */
export function SqlBlock({
  sql,
  label = "查看 SQL（点击展开/收起）",
}: {
  sql: string;
  label?: string;
}) {
  return (
    <details className="group rounded-lg border border-[var(--border-default)]">
      <summary className="cursor-pointer px-4 py-2 text-sm text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]">
        {label}
      </summary>
      <pre className="overflow-x-auto border-t border-[var(--border-default)] bg-[var(--bg-code)] px-4 py-3 font-mono text-xs text-[var(--accent-emerald)]">
        {sql}
      </pre>
    </details>
  );
}

/** 数据表：最多展示 maxRows 行，超出时底部提示截断 */
export function DataTable({
  columns,
  rows,
  row_count,
  truncated,
  maxRows = 50,
}: {
  columns: string[];
  rows: (string | number | null)[][];
  row_count?: number;
  truncated?: boolean;
  maxRows?: number;
}) {
  const tableRows = rows.slice(0, maxRows);
  const total = row_count ?? rows.length;
  const showMore = rows.length > maxRows || truncated === true;
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border-default)]">
      <table className="w-full text-left text-sm">
        <thead className="bg-[var(--bg-header)] text-[var(--text-secondary)]">
          <tr>
            {columns.map((c) => (
              <th key={c} className="px-4 py-2 font-medium whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tableRows.map((row, i) => (
            <tr
              key={i}
              className="border-t border-[var(--border-default)] text-[var(--text-secondary)]"
            >
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-1.5 whitespace-nowrap">
                  {cell === null ? "-" : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {showMore && (
        <p className="border-t border-[var(--border-default)] px-4 py-2 text-xs text-[var(--text-muted)]">
          仅显示前 {maxRows} 行，共 {total} 行
        </p>
      )}
    </div>
  );
}

function scoreColor(score: number): string {
  if (score >= 80) return "var(--status-ok)";
  if (score >= 60) return "var(--accent-sky)";
  return "var(--status-running)";
}

/** 分数环（0–100），匹配结果等场景复用 */
export function ScoreRing({ score }: { score: number }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const color = scoreColor(score);
  return (
    <svg viewBox="0 0 120 120" className="h-32 w-32" role="img" aria-label={`分数 ${score}`}>
      <circle
        cx="60"
        cy="60"
        r={r}
        fill="none"
        stroke="var(--chart-track)"
        strokeWidth="10"
      />
      <circle
        cx="60"
        cy="60"
        r={r}
        fill="none"
        stroke={color}
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
        fill={color}
      >
        {score}
      </text>
    </svg>
  );
}

/* ---------- 结果卡片 ---------- */

export type ResultCardProps = {
  final: FinalResult;
  title?: string;
  desc?: string;
  step?: string;
};

/**
 * 数据分析结果卡片：说明文案 + SQL + 图表 + 数据表。
 * 子组件 SqlBlock / DataTable / ScoreRing 可单独导出复用。
 */
export default function ResultCard({
  final,
  title = "分析结果",
  desc = "说明、图表与数据表",
  step = "④",
}: ResultCardProps) {
  const hasChart =
    final.chart && final.columns.length > 0 && final.rows.length > 0;

  return (
    <section className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-panel)] p-5">
      <div className="mb-4">
        <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold text-[var(--text-primary)]">
          <span className="inline-flex h-6 min-w-[1.5rem] items-center justify-center rounded-md bg-[var(--accent-sky-bg)] px-1.5 text-xs font-bold text-[var(--accent-sky)]">
            {step}
          </span>
          {title}
        </h3>
        {desc && (
          <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">{desc}</p>
        )}
      </div>

      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="inline-flex items-center rounded-full border border-[var(--accent-sky-border)] bg-[var(--accent-sky-bg)] px-2 py-0.5 text-xs font-medium text-[var(--accent-sky)]">
            引擎: {final.engine}
          </span>
          <span className="inline-flex items-center rounded-full border border-[var(--border-strong)] bg-[var(--bg-header)] px-2 py-0.5 text-xs font-medium text-[var(--text-secondary)]">
            耗时 {final.elapsed_ms} ms
          </span>
          <span className="inline-flex items-center rounded-full border border-[var(--border-strong)] bg-[var(--bg-header)] px-2 py-0.5 text-xs font-medium text-[var(--text-secondary)]">
            {final.row_count} 行{final.truncated ? "（已截断）" : ""}
          </span>
        </div>

        {final.explanation && (
          <p className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-4 py-3 text-sm leading-relaxed text-[var(--text-primary)]">
            {final.explanation}
          </p>
        )}

        {final.sql && <SqlBlock sql={final.sql} />}

        {hasChart && final.chart && (
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-code)] p-2">
            <ChartBox
              chart={final.chart}
              columns={final.columns}
              rows={final.rows}
            />
          </div>
        )}

        <DataTable
          columns={final.columns}
          rows={final.rows}
          row_count={final.row_count}
          truncated={final.truncated}
        />
      </div>
    </section>
  );
}
