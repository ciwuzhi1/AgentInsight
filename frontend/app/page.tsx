"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import ResultCard, { type FinalResult } from "@/components/ResultCard";
import { getToken, installAuthFetch, sseUrl } from "./auth-client";
import { Card, ErrorBar, OkBar } from "@/components/ui";
import { API_BASE, errText, readError, type MatchFinal, type SseEvent, type TraceStep, type DatasetInfo } from "@/components/shared";
import UploadPanel from "@/components/UploadPanel";
import ChatPanel from "@/components/ChatPanel";
import TimelinePanel from "@/components/TimelinePanel";
import MatchPanel from "@/components/MatchPanel";
import MatchResultPanel from "@/components/MatchResultPanel";
import HistoryPanel from "@/components/HistoryPanel";
import CrawlerPanel from "@/components/CrawlerPanel";

installAuthFetch();

export default function Home() {
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [final, setFinal] = useState<FinalResult | MatchFinal | null>(null);
  const [running, setRunning] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const reconnectCountRef = useRef<number>(0);
  const maxReconnects = 3;

  useEffect(() => {
    if (!getToken()) window.location.href = "/login";
  }, []);

  useEffect(() => {
    return () => esRef.current?.close();
  }, []);

  function subscribe(taskId: string) {
    esRef.current?.close();
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
        setEvents((prev) => [...prev, data]);
        if (data.terminal) {
          es.close();
          setRunning(false);
          setRetrying(false);
          setAskError(String(data.message ?? "任务执行失败"));
        } else {
          setRetrying(true);
          setAskError(String(data.message ?? "执行出错"));
        }
        return;
      }
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

    es.onerror = () => {
      es.close();
      if (reconnectCountRef.current < maxReconnects) {
        reconnectCountRef.current += 1;
        const delay = Math.min(1000 * 2 ** (reconnectCountRef.current - 1), 8000);
        setOkMsg(
          `事件流中断，${delay / 1000}s 后重连（第 ${reconnectCountRef.current}/${maxReconnects} 次）…`
        );
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
        const body = (await res.json().catch(() => ({}))) as {
          task_id?: string;
        };
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
        const body = (await res.json().catch(() => ({}))) as {
          task_id?: string;
        };
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

  function handleReplay(
    steps: TraceStep[],
    finalResult: MatchFinal | FinalResult | null
  ) {
    const evs: SseEvent[] = [];
    steps.forEach((r) => {
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
  }

  return (
    <div className="px-4 py-6">
      <header id="top" className="scroll-mt-20 pb-10">
        <h1 className="text-3xl font-bold tracking-tight text-[var(--text-primary)] sm:text-4xl">
          Agent
          <span className="bg-gradient-to-r from-sky-400 to-cyan-300 bg-clip-text text-transparent">
            Insight
          </span>
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-[var(--text-secondary)]">
          上传 CSV → 用自然语言提问 → 观察 Agent 时间线 →
          拿到图表与结论；也可以上传简历与岗位做匹配分析。后端：
          <span className="font-mono text-[var(--text-muted)]">{API_BASE}</span>
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <a href="#analysis" className="btn-primary">
            去分析 ↓
          </a>
          <Link href="/settings" className="btn-ghost">
            去设置
          </Link>
        </div>
      </header>

      <div className="space-y-10">
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
              <ChatPanel
                dataset={dataset}
                running={running}
                onStartTask={startTask}
              />
            </div>
            <div className="space-y-5 md:col-span-2">
              {askError && (!running || retrying) && (
                <ErrorBar message={askError} retrying={retrying} />
              )}
              {okMsg && <OkBar message={okMsg} />}
              <TimelinePanel events={events} running={running} />
              {final && final.engine !== "multi_agent" && (
                <ResultCard final={final as FinalResult} />
              )}
              <HistoryPanel onReplay={handleReplay} />
            </div>
          </div>
        </Card>

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

      <footer className="pb-6 pt-10 text-center text-xs text-[var(--text-faint)]">
        AgentInsight MVP · Next.js 15 + React 19 + Tailwind v4 + ECharts 5
      </footer>
    </div>
  );
}
