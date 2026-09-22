"use client";

import { useEffect, useRef, useState } from "react";
import ResultCard, { type FinalResult } from "@/components/ResultCard";
import { ensureAuthToken, installAuthFetch, sseUrl } from "./auth-client";
import { ErrorBar, OkBar } from "@/components/ui";
import { API_BASE, errText, type MatchFinal, type SseEvent, type TraceStep, type DatasetInfo } from "@/components/shared";
import UploadPanel from "@/components/UploadPanel";
import ChatPanel from "@/components/ChatPanel";
import TimelinePanel from "@/components/TimelinePanel";
import MatchPanel from "@/components/MatchPanel";
import MatchResultPanel from "@/components/MatchResultPanel";
import HistoryPanel from "@/components/HistoryPanel";
import CrawlerPanel from "@/components/CrawlerPanel";
import LogPanel, { makeLog, type LogEntry } from "@/components/LogPanel";
import { useWorkspace } from "./components/workspace-context";

installAuthFetch();

export default function Home() {
  const { view, setView } = useWorkspace();
  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [final, setFinal] = useState<FinalResult | MatchFinal | null>(null);
  const [running, setRunning] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [quickPrompt, setQuickPrompt] = useState<string | null>(null);
  const [matchLogs, setMatchLogs] = useState<LogEntry[]>([]);
  const [crawlerLogs, setCrawlerLogs] = useState<LogEntry[]>([]);
  const [taskKind, setTaskKind] = useState<"analysis" | "match" | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const reconnectCountRef = useRef<number>(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeTaskIdRef = useRef<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCountRef = useRef(0);
  const maxReconnects = 3;

  function notifyTaskStatus(running: boolean, kind: typeof taskKind) {
    window.dispatchEvent(
      new CustomEvent("agentinsight:task-status", {
        detail: { running, kind },
      })
    );
  }

  useEffect(() => {
    notifyTaskStatus(running, taskKind);
  }, [running, taskKind]);

  function pushMatchLog(text: string, level?: LogEntry["level"]) {
    setMatchLogs((prev) => [...prev.slice(-80), makeLog(text, level)]);
  }
  function pushCrawlerLog(text: string, level?: LogEntry["level"]) {
    setCrawlerLogs((prev) => [...prev.slice(-80), makeLog(text, level)]);
  }

  // 侧栏快捷提问：自动切到分析页
  useEffect(() => {
    if (quickPrompt) setView("analysis");
  }, [quickPrompt, setView]);

  useEffect(() => {
    function onPrompt(e: Event) {
      const q = (e as CustomEvent<string>).detail;
      setQuickPrompt(q);
    }
    window.addEventListener("agentinsight:quick-prompt", onPrompt);
    return () => window.removeEventListener("agentinsight:quick-prompt", onPrompt);
  }, []);

  useEffect(() => {
    return () => {
      esRef.current?.close();
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  function clearReconnectTimer() {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }

  function stopPolling() {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollCountRef.current = 0;
  }

  function closeEventStream() {
    clearReconnectTimer();
    stopPolling();
    esRef.current?.close();
    esRef.current = null;
    activeTaskIdRef.current = null;
  }

  /** SSE 失败兜底：ensureAuthToken + Bearer 轮询 GET /api/tasks/{id}，用 status/final_result/error 回填 */
  async function pollTaskStatus(taskId: string) {
    if (activeTaskIdRef.current !== taskId) return;
    if (pollCountRef.current >= 24) {
      setAskError("任务状态查询超时，请刷新或打开历史记录");
      setRunning(false);
      setOkMsg(null);
      return;
    }
    pollCountRef.current += 1;
    try {
      const token = await ensureAuthToken();
      const headers: Record<string, string> = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as {
        status?: string;
        error?: string | null;
        engine?: string | null;
        final_result?: FinalResult | MatchFinal | null;
      };
      if (activeTaskIdRef.current !== taskId) return;
      const st = body.status ?? "";
      if (st === "completed" || st.startsWith("failed")) {
        if (body.final_result) setFinal(body.final_result);
        if (st === "completed") {
          setOkMsg("后台任务已完成（SSE 中断后已通过查询同步结果）");
          setAskError(null);
        } else {
          setAskError(body.error || "任务执行失败");
        }
        setRunning(false);
        stopPolling();
        return;
      }
      if (st === "running" || st === "routing" || st === "created") {
        setOkMsg("事件流中断，后台仍在执行（执行中），已转为状态轮询…");
        pollTimerRef.current = setTimeout(() => void pollTaskStatus(taskId), 5000);
        return;
      }
      setAskError(body.error || `任务状态：${st || "未知"}`);
      setRunning(false);
      stopPolling();
    } catch (e) {
      if (activeTaskIdRef.current !== taskId) return;
      pollTimerRef.current = setTimeout(() => void pollTaskStatus(taskId), 5000);
      void e;
    }
  }

  function subscribe(taskId: string, opts?: { preserve?: boolean }) {
    clearReconnectTimer();
    stopPolling();
    esRef.current?.close();
    activeTaskIdRef.current = taskId;
    void ensureAuthToken().then((token) => {
      if (activeTaskIdRef.current !== taskId) return;
      if (!token) {
        setAskError("鉴权未就绪，无法订阅任务事件");
        setRunning(false);
        return;
      }
      openEventStream(taskId, opts);
    });
  }

  function openEventStream(taskId: string, opts?: { preserve?: boolean }) {
    if (!opts?.preserve) {
      setEvents([]);
      setFinal(null);
    }
    const es = new EventSource(sseUrl(`/api/tasks/${taskId}/events`));
    esRef.current = es;

    es.onmessage = (ev) => {
      reconnectCountRef.current = 0;
      let data: SseEvent;
      try {
        data = JSON.parse(ev.data) as SseEvent;
      } catch {
        return;
      }
      if (data.type === "final") {
        setFinal(data.result as FinalResult | MatchFinal);
        es.close();
        stopPolling();
        setRunning(false);
        setOkMsg(null);
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
      stopPolling();
      setRunning(false);
      setOkMsg(null);
    });

    es.onerror = () => {
      if (esRef.current !== es) return;
      es.close();
      if (reconnectCountRef.current < maxReconnects) {
        reconnectCountRef.current += 1;
        const delay = Math.min(1000 * 2 ** (reconnectCountRef.current - 1), 8000);
        setOkMsg(
          `事件流中断，${delay / 1000}s 后重连（第 ${reconnectCountRef.current}/${maxReconnects} 次）…`
        );
        reconnectTimerRef.current = setTimeout(() => {
          if (activeTaskIdRef.current === taskId) {
            subscribe(taskId, { preserve: true });
          }
        }, delay);
        return;
      }
      setRunning(true);
      setOkMsg("事件流重连耗尽，转为查询任务状态…");
      setAskError(null);
      stopPolling();
      pollCountRef.current = 0;
      void pollTaskStatus(taskId);
    };
  }

  async function startTask(query: string) {
    if (!dataset) {
      setAskError("请先在「数据集」页上传 CSV");
      return;
    }
    closeEventStream();
    setAskError(null);
    setRetrying(false);
    setOkMsg(null);
    setEvents([]);
    setFinal(null);
    setTaskKind("analysis");
    setRunning(true);
    try {
      await ensureAuthToken();
      const res = await fetch(`${API_BASE}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_id: dataset.dataset_id, query }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        task_id?: string;
        detail?: unknown;
      };
      if (res.status === 409) {
        if (body.task_id) {
          setOkMsg("已接管进行中任务");
          reconnectCountRef.current = 0;
          subscribe(body.task_id);
          return;
        }
        setAskError(
          typeof body.detail === "string" ? body.detail : "已有任务进行中"
        );
        setRunning(false);
        return;
      }
      if (!res.ok) {
        setAskError(
          typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`
        );
        setRunning(false);
        return;
      }
      reconnectCountRef.current = 0;
      subscribe(body.task_id as string);
    } catch (e) {
      setAskError(errText(e));
      setRunning(false);
    }
  }

  async function startMatch(resumeId: string, jobIds: number[]) {
    closeEventStream();
    setAskError(null);
    setRetrying(false);
    setOkMsg(null);
    setEvents([]);
    setFinal(null);
    setTaskKind("match");
    setRunning(true);
    try {
      await ensureAuthToken();
      const res = await fetch(`${API_BASE}/api/matches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_id: resumeId, job_ids: jobIds }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        task_id?: string;
        detail?: unknown;
      };
      if (res.status === 409) {
        if (body.task_id) {
          setOkMsg("已接管进行中任务");
          reconnectCountRef.current = 0;
          subscribe(body.task_id);
          return;
        }
        setAskError(
          typeof body.detail === "string" ? body.detail : "已有任务进行中"
        );
        setRunning(false);
        return;
      }
      if (!res.ok) {
        setAskError(
          typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`
        );
        setRunning(false);
        return;
      }
      reconnectCountRef.current = 0;
      subscribe(body.task_id as string);
    } catch (e) {
      setAskError(errText(e));
      setRunning(false);
    }
  }

  function handleReplay(
    steps: TraceStep[],
    finalResult: MatchFinal | FinalResult | null
  ) {
    closeEventStream();
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
    setTaskKind(
      finalResult && (finalResult as MatchFinal).engine === "multi_agent"
        ? "match"
        : "analysis"
    );
  }

  return (
    <div className="px-0 sm:px-1">
      {view === "dataset" && (
        <>
          <header className="pb-3">
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              数据集
              <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                上传 CSV，查看结构与规模
              </span>
            </h1>
            <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
              后端 <span className="font-mono">{API_BASE}</span>
            </p>
          </header>
          <div className="max-w-2xl">
            <UploadPanel dataset={dataset} setDataset={setDataset} />
            {dataset && (
              <button
                type="button"
                onClick={() => setView("analysis")}
                className="btn-primary mt-3 px-4 py-1.5 text-xs"
              >
                去数据分析 →
              </button>
            )}
          </div>
        </>
      )}

      {view === "analysis" && (
        <>
          <header className="flex flex-wrap items-center gap-2 pb-3">
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              数据分析
              <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                提问 → 时间线与图表
              </span>
            </h1>
            <button
              type="button"
              onClick={() => setView("dataset")}
              className="btn-ghost rounded-md border px-2 py-0.5 text-[11px]"
              style={{ borderColor: "var(--border-default)" }}
            >
              {dataset
                ? `数据集 ${dataset.dataset_id.slice(0, 8)}… · ${dataset.rows_estimate.toLocaleString()} 行`
                : "未上传数据集 · 去上传"}
            </button>
          </header>
          <div className="grid gap-3 lg:grid-cols-3">
            <div className="space-y-3 lg:col-span-1">
              <ChatPanel
                dataset={dataset}
                running={running}
                onStartTask={startTask}
                suggestedPrompt={quickPrompt}
              />
            </div>
            <div className="space-y-3 lg:col-span-2">
              {askError && (!running || retrying) && (
                <ErrorBar message={askError} retrying={retrying} />
              )}
              {okMsg && <OkBar message={okMsg} />}
              {/* 分析页空状态：无 dataset 时 CTA 直达数据集页 */}
              {!dataset && !running && events.length === 0 && !final && (
                <section className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-panel)] px-6 py-10 text-center">
                  <p className="text-sm font-medium text-[var(--text-primary)]">
                    尚未上传数据集
                  </p>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    上传 CSV 后即可提问，Agent 将翻译为 SQL 并实时展示执行过程
                  </p>
                  <button
                    type="button"
                    onClick={() => setView("dataset")}
                    className="btn-primary mt-4 px-4 py-1.5 text-xs"
                  >
                    去「数据集」页上传 →
                  </button>
                </section>
              )}
              {(taskKind === "analysis" || !taskKind) && (
                <TimelinePanel events={events} running={running && taskKind === "analysis"} />
              )}
              {final && final.engine !== "multi_agent" && (
                <ResultCard final={final as FinalResult} />
              )}
              <HistoryPanel onReplay={handleReplay} />
            </div>
          </div>
        </>
      )}

      {view === "match" && (
        <>
          <header className="pb-3">
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              简历匹配
              <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                上传简历 → 勾选岗位 → 多 Agent 打分
              </span>
            </h1>
          </header>
          <div className="grid gap-3 lg:grid-cols-[1fr_280px]">
            <div className="min-w-0 space-y-3">
              {askError && (!running || retrying) && (
                <ErrorBar message={askError} retrying={retrying} />
              )}
              {okMsg && <OkBar message={okMsg} />}
              <MatchPanel
                running={running}
                onStartMatch={(resumeId, jobIds) => {
                  pushMatchLog(
                    `发起匹配：${resumeId.slice(0, 8)}… × ${jobIds.length} 个岗位`
                  );
                  startMatch(resumeId, jobIds);
                }}
                onLog={pushMatchLog}
              />
              {final && final.engine === "multi_agent" && (
                <MatchResultPanel final={final as MatchFinal} />
              )}
              {(taskKind === "match" && (running || events.length > 0)) && (
                <TimelinePanel events={events} running={running} />
              )}
            </div>
            <LogPanel title="匹配日志" entries={matchLogs} className="lg:sticky lg:top-[calc(var(--topbar-h)+0.5rem)] lg:max-h-[calc(100vh-var(--topbar-h)-2rem)]" />
          </div>
        </>
      )}

      {view === "crawler" && (
        <>
          <header className="pb-3">
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              岗位爬虫
              <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">
                抓取 JD 入库并导出 CSV
              </span>
            </h1>
          </header>
          <div className="grid gap-3 lg:grid-cols-[1fr_280px]">
            <div className="min-w-0">
              <CrawlerPanel onLog={pushCrawlerLog} />
            </div>
            <LogPanel title="爬虫日志" entries={crawlerLogs} className="lg:sticky lg:top-[calc(var(--topbar-h)+0.5rem)] lg:max-h-[calc(100vh-var(--topbar-h)-2rem)]" />
          </div>
        </>
      )}
    </div>
  );
}
