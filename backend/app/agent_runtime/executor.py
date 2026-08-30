"""WorkflowExecutor：按计划 DAG 波次并行执行（CONTRACTS2 §1.3）。

设计决定：不 import persistence / app_settings —— 执行器只依赖 registry、
TaskState 与 core 配置；落库由 API 层在消费事件时完成。
"""
from __future__ import annotations

import asyncio
import time

from app.agent_runtime.message import make_message
from app.agent_runtime.planner import PlanError, PlanStep, validate_dag
from app.agent_runtime.registry import AgentRegistry, registry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import EmitFn
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 重试指数退避：0.5s 起、逐次翻倍、上限 4s
_RETRY_BASE_S = 0.5
_RETRY_CAP_S = 4.0


def _error_code(exc: Exception) -> str:
    """根据异常类名粗分 SSE error 事件的 code。"""
    name = type(exc).__name__
    if "LLM" in name:
        return "LLM_ERROR"
    if "SQL" in name or "Guard" in name:
        return "SQL_ERROR"
    if "Validation" in name:
        return "VALIDATION_ERROR"
    return "ENGINE_ERROR"


class WorkflowExecutor:
    """波次拓扑执行器：依赖齐备的步骤并行跑，下一波等上一波全部完成。"""

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        self._registry = agent_registry or registry

    async def execute(self, state: TaskState, emit: EmitFn) -> TaskState:
        """执行 state.plan_steps 描述的 DAG；全部成功则组装并广播 final。"""
        t0 = time.perf_counter()
        steps = validate_dag(state.plan_steps, settings.MAX_AGENT_STEPS)
        waves = self._build_waves(steps)
        order = {s.id: i + 1 for i, s in enumerate(steps)}
        done: dict[str, str] = {}  # step id -> "ok" | "skipped"

        for wave in waves:
            if state.status is TaskStatus.FAILED_FINAL:
                break
            await asyncio.gather(
                *(self._run_step(state, emit, s, steps, order, done) for s in wave)
            )

        if state.status is TaskStatus.FAILED_FINAL:
            return state
        state.transition(TaskStatus.VALIDATING)
        return await self._finish(state, emit, t0)

    @staticmethod
    def _build_waves(steps: list[PlanStep]) -> list[list[PlanStep]]:
        """按依赖分层；每波内步骤可并行（validate_dag 已保证可推进）。"""
        remaining = list(steps)
        finished: set[str] = set()
        waves: list[list[PlanStep]] = []
        while remaining:
            wave = [s for s in remaining if all(d in finished for d in s.depends_on)]
            if not wave:  # pragma: no cover - validate_dag 已排除
                raise PlanError("计划依赖无法推进")
            for s in wave:
                finished.add(s.id)
            waves.append(wave)
            remaining = [s for s in remaining if s.id not in finished]
        return waves

    async def _run_step(
        self,
        state: TaskState,
        emit: EmitFn,
        step: PlanStep,
        steps: list[PlanStep],
        order: dict[str, int],
        done: dict[str, str],
    ) -> None:
        """执行单个步骤：重试退避、消息包装、optional 跳过或终态失败。"""
        name = step.agent
        attempt = 0
        while True:
            state.current_agent = name
            state.current_step = order[step.id]
            await emit({"type": "agent_start", "agent": name, "step": step.id})
            start = time.perf_counter()

            result = None
            exc: Exception | None = None
            error_msg: str | None = None
            try:
                result = await self._registry.get(name).run(state, emit)
                if result.status != "ok":
                    error_msg = "; ".join(result.errors) or f"{name} returned error"
            except Exception as e:  # noqa: BLE001 - 统一进重试/失败分流
                exc = e
                error_msg = str(e)

            if error_msg is None:
                await self._succeed(state, emit, step, steps, done, result, start)
                return

            # 失败：记录错误并决定重试 / 跳过 / 终态失败
            attempt += 1
            state.errors.append(f"{name}: {error_msg}")
            latency_ms = int((time.perf_counter() - start) * 1000)
            status = "error" if result is None else result.status
            code = _error_code(exc) if exc is not None else "ENGINE_ERROR"
            await emit(
                {
                    "type": "agent_end",
                    "agent": name,
                    "step": step.id,
                    "latency_ms": latency_ms,
                    "status": status,
                }
            )
            await emit({"type": "error", "code": code, "message": error_msg})

            if attempt <= settings.MAX_RETRY:
                state.retry_count = attempt
                delay = min(_RETRY_BASE_S * 2 ** (attempt - 1), _RETRY_CAP_S)
                await asyncio.sleep(delay)
                await emit(
                    {
                        "type": "retry",
                        "step": step.id,
                        "agent": name,
                        "retry_count": attempt,
                    }
                )
                continue

            # 重试耗尽：optional 步骤跳过，否则任务终态失败
            if step.params.get("optional"):
                done[step.id] = "skipped"
                await emit({"type": "step_skipped", "step": step.id, "reason": error_msg})
                logger.warning("可选步骤失败已跳过 task=%s step=%s: %s", state.task_id, step.id, error_msg)
                return
            self._fail_final(state)
            return

    async def _succeed(
        self,
        state: TaskState,
        emit: EmitFn,
        step: PlanStep,
        steps: list[PlanStep],
        done: dict[str, str],
        result,
        start: float,
    ) -> None:
        """成功收尾：结果落盘、AgentMessage 入列、agent_end 带 detail。"""
        latency_ms = int((time.perf_counter() - start) * 1000)
        state.results[step.agent] = result.data
        downstream = [s.agent for s in steps if step.id in s.depends_on]
        msg = make_message(
            task_id=state.task_id,
            sender=step.agent,
            receiver=downstream,  # 下游 agent 名列表
            type=result.message_type,
            payload=result.data,
        )
        state.messages.append(msg)
        await emit(
            {
                "type": "agent_end",
                "agent": step.agent,
                "step": step.id,
                "latency_ms": latency_ms,
                "status": "ok",
                "detail": {
                    "message_id": msg.message_id,
                    "message_type": result.message_type,
                },
            }
        )
        done[step.id] = "ok"

    @staticmethod
    def _fail_final(state: TaskState) -> None:
        """必经步骤重试耗尽：进入 failed_final 终态（并发下幂等）。"""
        if state.status is TaskStatus.RUNNING:
            state.transition(TaskStatus.FAILED)
        if state.status is TaskStatus.FAILED:
            state.transition(TaskStatus.FAILED_FINAL)

    async def _finish(self, state: TaskState, emit: EmitFn, t0: float) -> TaskState:
        """写入 final_result、迁移到 completed 并广播 final 事件。"""
        final = self._assemble_final(state)
        if isinstance(final, dict):
            final.setdefault("elapsed_ms", int((time.perf_counter() - t0) * 1000))
        state.final_result = final
        state.transition(TaskStatus.COMPLETED)
        await emit({"type": "final", "result": final})
        return state

    @staticmethod
    def _assemble_final(state: TaskState) -> dict | None:
        """匹配链路用 §2.1 匹配形态，否则沿用数据形态（results['data_agent']['final']）。"""
        match = state.results.get("match_agent")
        if isinstance(match, dict) and match:
            return {
                "task_id": state.task_id,
                "query": state.query,
                "engine": "multi_agent",
                "resume": match.get("resume") or {},
                "jobs": match.get("jobs") or [],
                "score": match.get("score"),
                "dimensions": match.get("dimensions") or {},
                "skill_gap": match.get("skill_gap") or [],
                "interpretation": match.get("interpretation") or "",
                "interpretation_source": match.get("interpretation_source") or "mock",
            }
        data = state.results.get("data_agent") or {}
        final = data.get("final") if isinstance(data, dict) else None
        if not isinstance(final, dict):
            final = data or None
        if isinstance(final, dict):
            final.setdefault("task_id", state.task_id)
            final.setdefault("query", state.query)
        return final
