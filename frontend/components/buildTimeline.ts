/* SSE 事件流 → TimelineCard 所需的 steps / items 模型 */

import type { StepCard, TimelineItem } from "./TimelineCard";
import type { SseEvent, TimelineModel } from "./shared";

export function buildTimeline(events: SseEvent[]): TimelineModel {
  let hasPlan = false;
  let steps: StepCard[] = [];
  const byId = new Map<string, StepCard>();
  const items: TimelineItem[] = [];

  const ensureCard = (stepId: string, agent: string): StepCard => {
    let card = byId.get(stepId);
    if (!card) {
      card = {
        step_id: stepId,
        agent,
        depends_on: [],
        status: "running",
        retries: 0,
      };
      byId.set(stepId, card);
      steps.push(card);
    }
    return card;
  };

  // 优先按 step id 配对；没有 step id 时退化为按 agent 名找运行中的卡
  const matchCard = (ev: SseEvent): StepCard | undefined => {
    const stepId =
      ev.step !== undefined && ev.step !== null ? String(ev.step) : "";
    if (stepId) {
      const card = byId.get(stepId);
      if (card) return card;
    }
    const agent = String(ev.agent ?? "");
    return steps.find((s) => s.agent === agent);
  };

  for (const ev of events) {
    switch (ev.type) {
      case "plan": {
        hasPlan = true;
        steps = [];
        byId.clear();
        const raw = Array.isArray(ev.steps)
          ? (ev.steps as Record<string, unknown>[])
          : [];
        for (const s of raw) {
          const card: StepCard = {
            step_id: String(s.id ?? ""),
            agent: String(s.agent ?? ""),
            depends_on: Array.isArray(s.depends_on)
              ? (s.depends_on as unknown[]).map(String)
              : [],
            status: "pending",
            retries: 0,
          };
          byId.set(card.step_id, card);
          steps.push(card);
        }
        break;
      }
      case "agent_start": {
        if (hasPlan) {
          const stepId =
            ev.step !== undefined && ev.step !== null ? String(ev.step) : "";
          const card = ensureCard(
            stepId || String(ev.agent ?? ""),
            String(ev.agent ?? "")
          );
          card.status = "running";
        } else {
          // 旧链路（无 plan）：平铺
          items.push({
            kind: "agent",
            agent: String(ev.agent ?? ""),
            status: "running",
          });
        }
        break;
      }
      case "agent_end": {
        const status = String(ev.status ?? "ok");
        const latency = Number(ev.latency_ms ?? 0);
        if (hasPlan) {
          const card = matchCard(ev);
          if (card) {
            card.status = status === "ok" ? "ok" : "error";
            card.statusText = status === "ok" ? undefined : status;
            card.latency_ms = latency;
          }
        } else {
          const target = [...items]
            .reverse()
            .find(
              (i) =>
                i.kind === "agent" &&
                i.agent === ev.agent &&
                i.status === "running"
            );
          if (target) {
            target.status = status;
            target.latency_ms = latency;
          } else {
            items.push({
              kind: "agent",
              agent: String(ev.agent ?? ""),
              status,
              latency_ms: latency,
            });
          }
        }
        break;
      }
      case "retry": {
        if (hasPlan) {
          const card = matchCard(ev);
          if (card) {
            card.retries = Math.max(
              card.retries,
              Number(ev.retry_count ?? 0) || card.retries + 1
            );
          }
        }
        break;
      }
      case "step_skipped": {
        if (hasPlan) {
          const stepId =
            ev.step !== undefined && ev.step !== null ? String(ev.step) : "";
          const card = stepId
            ? byId.get(stepId)
            : steps.find((s) => s.agent === String(ev.agent ?? ""));
          if (card) {
            card.status = "skipped";
          }
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
      case "cache":
        items.push({
          kind: "cache",
          hit: ev.hit === true,
          key:
            ev.key !== undefined && ev.key !== null ? String(ev.key) : undefined,
        });
        break;
      default:
        break;
    }
  }
  return { hasPlan, steps, items };
}
