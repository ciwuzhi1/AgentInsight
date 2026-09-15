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

/* ---------- 辅助 ---------- */

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export async function readError(res: Response): Promise<Error> {
  const detail = await res.json().catch(() => ({}));
  const d = (detail as { detail?: string }).detail;
  return new Error(d ?? `HTTP ${res.status}`);
}
