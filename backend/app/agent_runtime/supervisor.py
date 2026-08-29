"""Supervisor：路由决策与任务编排（契约 §3.5）。

设计决定：本模块不 import app.persistence / mysql —— Agent Runtime 与
存储完全解耦，任务/步骤/事件的落库由 API 层（CODE-4）在消费 SSE 事件
或调用 run_task 返回后完成。supervisor 只负责状态机推进与 emit 事件。
"""
from __future__ import annotations

import time

from app.agent_runtime.registry import AgentRegistry, registry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import EmitFn
from app.core.config import settings

# 求职意图关键词；命中说明本轮问题与"简历-岗位匹配"相关，
# 预留未来 resume_future 专属 agent，MVP 阶段仍交给 data_agent
_RESUME_KEYWORDS = ("简历", "岗位", "匹配")


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


class Supervisor:
    """按 plan 顺序调度 agent 并推进 TaskState。"""

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        self._registry = agent_registry or registry

    async def route(self, state: TaskState, emit: EmitFn) -> list[str]:
        """MVP 规则路由，返回本任务的 agent 执行计划。

        预留扩展：后续可在此接入 complexity 评估（如查询需多步分解、
        多 agent 协作时生成更长的 plan），当前为单 agent 直通。
        """
        if not state.dataset_id:
            # 无数据集：由 clarify 路径直接产出提示性 final_result
            return ["clarify"]
        if any(kw in state.query for kw in _RESUME_KEYWORDS):
            # 求职意图：标记 mark，本轮仍走 data_agent
            state.context["mark"] = "resume_future"
        return ["data_agent"]

    async def run_task(self, state: TaskState, emit: EmitFn) -> TaskState:
        """完整编排：路由 → 逐步执行 plan → 校验 → 组装 final_result。"""
        t0 = time.perf_counter()
        state.transition(TaskStatus.ROUTING)
        await emit({"type": "state", "status": "routing", "task_id": state.task_id})

        state.plan = await self.route(state, emit)
        state.transition(TaskStatus.RUNNING)

        # clarify 路径：无 dataset，跳过 agent 执行，直接组装提示结果
        if state.plan == ["clarify"]:
            final = {
                "task_id": state.task_id,
                "query": state.query,
                "engine": None,
                "sql": None,
                "explanation": "请先上传数据集后再提问",
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "chart": None,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            }
            # clarify 组装视作校验环节，保证迁移链合法
            state.transition(TaskStatus.VALIDATING)
            return await self._finish(state, emit, final, t0)

        # 按 plan 顺序执行主 agent；任一步重试耗尽则整体失败
        for step, name in enumerate(state.plan, start=1):
            ok = await self._exec_step(state, emit, name, step, t0)
            if not ok:
                return state

        # 主 agent 成功后进入校验阶段
        state.transition(TaskStatus.VALIDATING)
        try:
            has_validator = "validator_agent" in self._registry.names()
        except Exception:  # pragma: no cover - 防御性
            has_validator = False
        if has_validator:
            # validator_agent 未注册时跳过校验（MVP 允许无校验运行）
            step = len(state.plan) + 1
            ok = await self._exec_step(
                state, emit, "validator_agent", step, t0, expect_validating=True
            )
            if not ok:
                return state

        # 组装 final_result：优先取 data_agent 放好的 §6 结构
        data = state.results.get("data_agent") or {}
        final = data.get("final") if isinstance(data, dict) else None
        if not isinstance(final, dict):
            final = data or None
        if isinstance(final, dict):
            final.setdefault("task_id", state.task_id)
            final.setdefault("query", state.query)
        return await self._finish(state, emit, final, t0)

    async def _exec_step(
        self,
        state: TaskState,
        emit: EmitFn,
        name: str,
        step: int,
        t0: float,
        expect_validating: bool = False,
    ) -> bool:
        """执行单个 agent 步骤：计时、事件、结果落盘与重试。

        返回 False 表示重试耗尽、任务已进 failed_final。
        expect_validating=True 用于 validator：重试成功后需迁回 validating。
        """
        while True:
            state.current_agent = name
            state.current_step = step
            await emit({"type": "agent_start", "agent": name, "step": step})
            start = time.perf_counter()
            result = None
            try:
                result = await self._registry.get(name).run(state, emit)
            except Exception as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                state.errors.append(f"{name}: {exc}")
                await emit(
                    {
                        "type": "agent_end",
                        "agent": name,
                        "latency_ms": latency_ms,
                        "status": "error",
                    }
                )
                await emit({"type": "error", "code": _error_code(exc), "message": str(exc)})
                if not await self._enter_retry(state, emit, name):
                    return False
                continue

            latency_ms = int((time.perf_counter() - start) * 1000)
            state.results[name] = result.data
            await emit(
                {
                    "type": "agent_end",
                    "agent": name,
                    "latency_ms": latency_ms,
                    "status": result.status,
                }
            )
            if result.status != "ok":
                state.errors.extend(result.errors)
                message = "; ".join(result.errors) or f"{name} returned error"
                await emit({"type": "error", "code": "ENGINE_ERROR", "message": message})
                if not await self._enter_retry(state, emit, name):
                    return False
                continue

            if expect_validating and state.status is TaskStatus.RUNNING:
                # 重试路径把状态拉回 running，validator 成功后迁回 validating
                state.transition(TaskStatus.VALIDATING)
            return True

    async def _enter_retry(self, state: TaskState, emit: EmitFn, name: str) -> bool:
        """异常后决定重试或终态失败；返回 True 表示将重跑当前步骤。"""
        state.transition(TaskStatus.FAILED)
        if state.retry_count < settings.MAX_RETRY:
            state.retry_count += 1
            state.transition(TaskStatus.RETRYING)
            state.transition(TaskStatus.RUNNING)
            await emit({"type": "retry", "agent": name, "retry_count": state.retry_count})
            return True
        state.transition(TaskStatus.FAILED_FINAL)
        return False

    async def _finish(
        self, state: TaskState, emit: EmitFn, final: dict | None, t0: float
    ) -> TaskState:
        """写入 final_result、迁移到 completed 并广播 final 事件。"""
        if isinstance(final, dict):
            final.setdefault("elapsed_ms", int((time.perf_counter() - t0) * 1000))
        state.final_result = final
        state.transition(TaskStatus.COMPLETED)
        await emit({"type": "final", "result": final})
        return state
