"""Supervisor：路由决策与任务编排（CONTRACTS2 §1.4）。

设计决定：本模块不 import app.persistence / app_settings —— Agent Runtime 与
存储/设置中心完全解耦，任务/步骤/事件的落库由 API 层在消费 SSE 事件时完成。
执行细节委托 WorkflowExecutor，本模块只负责路由、plan 事件与 clarify 短路。
"""
from __future__ import annotations

import asyncio
import time

from app.agent_runtime.executor import WorkflowExecutor, force_fail_final
from app.agent_runtime.planner import PlanStep, build_data_plan, build_match_plan
from app.agent_runtime.registry import AgentRegistry, registry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import EmitFn
from app.core.logging import get_logger

logger = get_logger(__name__)

# 求职意图关键词；命中说明本轮问题与"简历-岗位匹配"相关
_RESUME_KEYWORDS = ("简历", "岗位", "匹配")

# 任务总超时（秒）：路由 + 全部步骤（含重试）的硬上限。
# 单步 _STEP_TIMEOUT_S=120 × (1+MAX_RETRY) 已覆盖单步挂起；此处兜底
# 多步串行 / emit 落库卡死 / 事件循环饿死等，超时后强制 failed_final 并写 error。
_TASK_TIMEOUT_S = 600


class Supervisor:
    """路由生成计划（list[PlanStep]），并委托执行器推进任务。"""

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        self._registry = agent_registry or registry

    async def route(self, state: TaskState, emit: EmitFn) -> list[PlanStep]:
        """按上下文选择执行计划。

        路由逻辑：resume/dataset 有无分流 + query 复杂度评估（简单问题跳过 validator）。
        """
        has_resume = bool(state.context.get("resume"))
        if has_resume and (
            any(kw in state.query for kw in _RESUME_KEYWORDS) or not state.query
        ):
            # 匹配链路：resume/job 并行 → match → validator
            return build_match_plan()
        if not state.dataset_id and not has_resume:
            # 无数据集：clarify 短路（空计划，run_task 里直接产出提示结果）
            return []
        if any(kw in state.query for kw in _RESUME_KEYWORDS):
            # 求职意图但本轮走数据链路：标记预留
            state.context["mark"] = "resume_future"
        return build_data_plan(state.query)

    async def run_task(self, state: TaskState, emit: EmitFn) -> TaskState:
        """完整编排：路由 → 广播 plan → 委托 WorkflowExecutor 执行。

        带总超时看门狗：超时后 force_fail_final 并发 terminal error，
        保证 update_task 能写到 failed_final + 明确 error，而不是永远 routing。
        """
        t0 = time.perf_counter()
        # 并发重复调用同一 state 时，CREATED→ROUTING 会抛 ValueError；
        # 这里吞掉并沿用当前状态，让幂等锁/后续阶段去判定，而不是裸崩。
        try:
            state.transition(TaskStatus.ROUTING)
        except ValueError:
            if state.status in (TaskStatus.COMPLETED, TaskStatus.FAILED_FINAL):
                logger.warning("run_task 重复调用且已终态 task=%s status=%s", state.task_id, state.status)
                return state
            logger.warning("run_task 重复调用 task=%s status=%s，继续当前状态", state.task_id, state.status)
        await emit({"type": "state", "status": "routing", "task_id": state.task_id})

        try:
            return await asyncio.wait_for(
                self._run_phases(state, emit, t0), timeout=_TASK_TIMEOUT_S
            )
        except (asyncio.TimeoutError, TimeoutError):
            # 看门狗超时：强制终态 + 明确 error（含当前 agent / 阶段）。
            # terminal error 由 API 层 _run 统一补发，这里只保证状态与 errors 就绪。
            agents = "、".join(sorted(state.current_agents)) or "unknown_agent"
            phase = "routing" if state.status is TaskStatus.ROUTING else "running"
            msg = (
                f"{agents} 任务总超时（超过 {_TASK_TIMEOUT_S} 秒，阶段 {phase}）："
                f"已中止执行，请稍后重试或缩小查询范围"
            )
            state.errors.append(msg)
            state.context["error_code"] = "TIMEOUT"
            state.context["error_reason"] = "task_timeout"
            force_fail_final(state)
            # 超时失败不写 final_result（避免被误认为 completed 结果）
            logger.warning("任务总超时 task=%s: %s", state.task_id, msg)
            return state
        except asyncio.CancelledError:
            # 取消也必须到达终态，否则 status 永停 routing/running
            msg = "任务已取消"
            state.errors.append(msg)
            state.context["error_code"] = "CANCELLED"
            state.context["error_reason"] = "cancelled"
            force_fail_final(state)
            logger.warning("任务被取消 task=%s", state.task_id)
            raise

    async def _run_phases(self, state: TaskState, emit: EmitFn, t0: float) -> TaskState:
        """路由 → plan → 执行（不含总超时包装）。"""
        try:
            steps = await self.route(state, emit)
        except Exception as exc:
            # 路由阶段异常：不能让状态停在 routing，强制终态并写 error
            raw = (str(exc) or "").strip() or type(exc).__name__
            msg = f"路由失败：{raw}"
            state.errors.append(msg)
            state.context["error_code"] = "ENGINE_ERROR"
            state.context["error_reason"] = "route_error"
            force_fail_final(state)
            logger.exception("路由失败 task=%s", state.task_id)
            return state

        state.plan_steps = steps
        # plan 语义不变：agent 名单（clarify 时保留原名）
        state.plan = [s.agent for s in steps] if steps else ["clarify"]

        # clarify 路径：无 dataset 无 resume，跳过执行直接组装提示结果
        if not steps:
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
            try:
                state.transition(TaskStatus.VALIDATING)
            except ValueError:
                if state.status in (TaskStatus.COMPLETED, TaskStatus.FAILED_FINAL):
                    logger.warning("clarify VALIDATING 迁移被跳过 task=%s", state.task_id)
                    return state
                raise
            return await self._finish(state, emit, final, t0)

        await emit(
            {
                "type": "plan",
                "steps": [
                    {
                        "id": s.id,
                        "agent": s.agent,
                        "depends_on": list(s.depends_on),
                    }
                    for s in steps
                ],
            }
        )
        state.transition(TaskStatus.RUNNING)
        # 同步 DB 状态：避免 tasks.status 一直停在 routing（可观测契约）
        await emit({"type": "state", "status": "running", "task_id": state.task_id})
        try:
            return await WorkflowExecutor(self._registry).execute(state, emit)
        except Exception as exc:
            # 执行阶段未捕获异常（如 PlanError）：强制终态，避免卡在 running
            raw = (str(exc) or "").strip() or type(exc).__name__
            agents = "、".join(sorted(state.current_agents)) or "unknown_agent"
            msg = f"{agents} 执行失败：{raw}"
            state.errors.append(msg)
            state.context["error_code"] = "ENGINE_ERROR"
            state.context["error_reason"] = "execute_error"
            force_fail_final(state)
            logger.exception("执行阶段异常 task=%s", state.task_id)
            return state

    async def _finish(
        self, state: TaskState, emit: EmitFn, final: dict | None, t0: float
    ) -> TaskState:
        """写入 final_result、迁移到 completed 并广播 final 事件。"""
        if isinstance(final, dict):
            final.setdefault("elapsed_ms", int((time.perf_counter() - t0) * 1000))
        try:
            state.transition(TaskStatus.COMPLETED)
        except ValueError:
            # 竞态：已被 force_fail_final → 幂等返回；重复完成等仍抛
            if state.status is TaskStatus.FAILED_FINAL:
                logger.warning("COMPLETED 迁移被跳过 task=%s status=%s", state.task_id, state.status)
                if state.final_result is None:
                    state.final_result = final
                return state
            raise
        state.final_result = final
        await emit({"type": "final", "result": final})
        return state
