"""Supervisor：路由决策与任务编排（CONTRACTS2 §1.4）。

设计决定：本模块不 import app.persistence / app_settings —— Agent Runtime 与
存储/设置中心完全解耦，任务/步骤/事件的落库由 API 层在消费 SSE 事件时完成。
执行细节委托 WorkflowExecutor，本模块只负责路由、plan 事件与 clarify 短路。
"""
from __future__ import annotations

import time

from app.agent_runtime.executor import WorkflowExecutor
from app.agent_runtime.planner import PlanStep, build_data_plan, build_match_plan
from app.agent_runtime.registry import AgentRegistry, registry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import EmitFn

# 求职意图关键词；命中说明本轮问题与"简历-岗位匹配"相关
_RESUME_KEYWORDS = ("简历", "岗位", "匹配")


class Supervisor:
    """路由生成计划（list[PlanStep]），并委托执行器推进任务。"""

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        self._registry = agent_registry or registry

    async def route(self, state: TaskState, emit: EmitFn) -> list[PlanStep]:
        """按上下文选择执行计划。

        预留扩展：后续可在此接入 complexity 评估（多步分解、多 agent 协作时
        生成更长计划），当前仅按 resume/dataset 有无分流。
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
        return build_data_plan()

    async def run_task(self, state: TaskState, emit: EmitFn) -> TaskState:
        """完整编排：路由 → 广播 plan → 委托 WorkflowExecutor 执行。"""
        t0 = time.perf_counter()
        state.transition(TaskStatus.ROUTING)
        await emit({"type": "state", "status": "routing", "task_id": state.task_id})

        steps = await self.route(state, emit)
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
            state.transition(TaskStatus.VALIDATING)
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
        return await WorkflowExecutor(self._registry).execute(state, emit)

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
