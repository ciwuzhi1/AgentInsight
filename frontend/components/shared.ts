/* 共享类型与 API 辅助（page.tsx 与各面板复用） */

import type { StepCard, TimelineItem } from "./TimelineCard";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

/* ---------- 类型 ---------- */

export type SchemaCol = { name: string; type: string };

export type DatasetInfo = {
  dataset_id: string;
  name: string;
  table_name: string;
  schema: SchemaCol[];
  rows_estimate: number;
  size_mb: number;
  engine_hint: string;
};

export type SseEvent = { type: string } & Record<string, unknown>;

export type TimelineModel = {
  hasPlan: boolean;
  steps: StepCard[];
  items: TimelineItem[];
};

/** 匹配形态 final（CONTRACTS2 §2.1） */
export type MatchFinal = {
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

export type JobItem = {
  title: string;
  company: string | null;
  location: string | null;
  skills: string[];
};

/** 爬虫岗位库里的岗位（带 id，供匹配多选） */
export type CrawlerJobItem = JobItem & { id: number };

export type TraceStep = {
  agent_name: string;
  status: string;
  latency_ms: number | null;
  detail: Record<string, unknown> | null;
};

export type HistoryItem = {
  id: string;
  query: string;
  status: string;
  engine: string | null;
  score: number | null;
  created_at: string;
};

export const DIM_LABELS: Record<string, string> = {
  skill: "技能",
  project: "项目",
  experience: "经验",
  education: "学历",
  engineering: "工程",
};

/** 快捷提问预设：Sidebar 与 ChatPanel 的单一来源 */
export const JOB_PROMPT_PRESETS = [
  "JD 中需求最多的技能 Top 10 是什么？",
  "统计各城市的岗位数量和平均薪资，按数量降序",
  "要求 Python 的岗位里，哪个城市平均薪资最高？",
  "各薪资区间（salary_k）的岗位数量分布",
  "'Python+SQL' 同时出现的岗位有多少？",
  "对比北京和上海岗位的技能要求差异",
  "最近发布（2025-05 之后）的岗位最常见 5 项技能",
  "岗位数量最多的公司 Top 5",
];

/* ---------- 辅助 ---------- */

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export async function readError(res: Response): Promise<Error> {
  const detail = await res.json().catch(() => ({}));
  const d = (detail as { detail?: string }).detail;
  return new Error(d ?? `HTTP ${res.status}`);
}
