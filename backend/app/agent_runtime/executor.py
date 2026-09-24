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
from app.agents.base import AgentResult, EmitFn
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 重试指数退避：0.5s 起、逐次翻倍、上限 4s
_RETRY_BASE_S = 0.5
_RETRY_CAP_S = 4.0

# 单步执行超时（秒）：防止 LLM/MinerU 挂起拖死整波
_STEP_TIMEOUT_S = 120


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


def _classify_code(error_msg: str | None, exc: Exception | None) -> str:
    """失败事件 code：Timeout / LLM / SQL 等，异常类优先，其次文案启发。"""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "TIMEOUT"
    if exc is not None:
        return _error_code(exc)
    msg = error_msg or ""
    if "LLM" in msg:
        return "LLM_ERROR"
    if "超时" in msg:
        return "TIMEOUT"
    if "SQL" in msg or "Guard" in msg:
        return "SQL_ERROR"
    return "ENGINE_ERROR"


def _format_step_error(name: str, raw: str | None, exc: Exception | None) -> str:
    """步骤错误中文可读化：Timeout/LLM 单独措辞；保证非空且含 agent 名。

    asyncio.TimeoutError / TimeoutError 的 str() 常为空，必须单独生成文案，
    否则 failed_final 落库的 tasks.error 为空串，前端无法展示。
    """
    raw = (raw or "").strip()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"{name} 执行超时（单步超过 {_STEP_TIMEOUT_S} 秒）"
    if exc is not None and "Timeout" in type(exc).__name__:
        return f"{name} 执行超时（单步超过 {_STEP_TIMEOUT_S} 秒）"
    if exc is not None and "LLM" in type(exc).__name__:
        reason = raw or str(exc).strip() or "未知原因"
        low = reason.lower()
        if "timeout" in low or "timed out" in low or "超时" in reason:
            return f"{name} LLM 调用超时：{reason}"
        if reason.startswith("LLM"):
            return f"{name} {reason}"
        return f"{name} LLM 调用失败：{reason}"
    reason = raw or (str(exc).strip() if exc is not None else "") or "未知错误"
    return f"{name}：{reason}"


def _error_reason(error_msg: str | None, exc: Exception | None) -> str:
    """步骤失败 reason 摘要（写入 task_steps.detail.reason，便于前端/日志归类）。"""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if exc is not None and "Timeout" in type(exc).__name__:
        return "timeout"
    if exc is not None and "LLM" in type(exc).__name__:
        msg = (error_msg or "") + " " + (str(exc) or "")
        low = msg.lower()
        if "timeout" in low or "timed out" in low or "超时" in msg:
            return "llm_timeout"
        return "llm_error"
    code = _classify_code(error_msg, exc)
    return {
        "TIMEOUT": "timeout",
        "LLM_ERROR": "llm_error",
        "SQL_ERROR": "sql_error",
        "VALIDATION_ERROR": "validation_error",
    }.get(code, "step_error")


def force_fail_final(state: TaskState) -> None:
    """任意非终态强制进入 failed_final（走合法迁移链；并发下幂等）。

    覆盖 CREATED/ROUTING/RUNNING/VALIDATING/FAILED/RETRYING——
    特别是异常发生在 routing 阶段时，_TRANSITIONS 不允许 ROUTING→FAILED，
    必须经 RUNNING 再进终态，否则任务永远停在 routing。
    """
    if state.status in (TaskStatus.COMPLETED, TaskStatus.FAILED_FINAL):
        return
    try:
        if state.status is TaskStatus.CREATED:
            state.transition(TaskStatus.ROUTING)
        if state.status is TaskStatus.ROUTING:
            state.transition(TaskStatus.RUNNING)
        if state.status is TaskStatus.RETRYING:
            state.transition(TaskStatus.FAILED_FINAL)
            return
        if state.status in (TaskStatus.RUNNING, TaskStatus.VALIDATING):
            state.transition(TaskStatus.FAILED)
        if state.status is TaskStatus.FAILED:
            state.transition(TaskStatus.FAILED_FINAL)
    except ValueError:
        # 并发兄弟步骤可能已推进到终态；保持幂等不抛
        pass


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
            results = await asyncio.gather(
                *(self._run_step(state, emit, s, steps, order, done) for s in wave),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, BaseException):
                    # 单步未捕获异常不再打断兄弟协程收尾；记录后由状态机判定终态
                    logger.exception("步骤协程未捕获异常 task=%s: %s", state.task_id, r)

        if state.status is TaskStatus.FAILED_FINAL:
            return state
        try:
            state.transition(TaskStatus.VALIDATING)
        except ValueError:
            if state.status in (TaskStatus.COMPLETED, TaskStatus.FAILED_FINAL):
                logger.warning("VALIDATING 迁移被跳过 task=%s status=%s", state.task_id, state.status)
                return state
            raise
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
        # 上游 optional 被跳过 → 本步自动跳过，避免缺数据继续跑
        if any(done.get(d) == "skipped" for d in step.depends_on):
            done[step.id] = "skipped"
            await emit(
                {
                    "type": "step_skipped",
                    "step": step.id,
                    "reason": "上游步骤已跳过",
                }
            )
            logger.info(
                "上游步骤已跳过，本步自动跳过 task=%s step=%s", state.task_id, step.id
            )
            return

        name = step.agent
        attempt = 0
        while True:
            # fail-fast：并发兄弟步骤已把任务推到终态，不再重试
            if state.status is TaskStatus.FAILED_FINAL:
                return
            state.current_agents.add(name)
            state.current_step = order[step.id]
            await emit({"type": "agent_start", "agent": name, "step": step.id})
            start = time.perf_counter()

            result = None
            exc: Exception | None = None
            error_msg: str | None = None
            try:
                result = await asyncio.wait_for(
                    self._registry.get(name).run(state, emit),
                    timeout=_STEP_TIMEOUT_S,
                )
                if result.status != "ok":
                    raw = "; ".join(result.errors) or f"{name} 返回错误"
                    error_msg = _format_step_error(name, raw, None)
            except Exception as e:  # noqa: BLE001 - 统一进重试/失败分流
                exc = e
                # TimeoutError 的 str() 可能为空，必须经 _format_step_error 生成中文文案
                error_msg = _format_step_error(name, str(e), e)

            if error_msg is None:
                await self._succeed(state, emit, step, steps, done, result, start)
                return

            # 失败：记录错误并决定重试 / 跳过 / 终态失败
            attempt += 1
            latency_ms = int((time.perf_counter() - start) * 1000)
            # 记住自己的下标：并发兄弟步骤也会 append，不能用 errors[-1] 回写
            err_idx = len(state.errors)
            state.errors.append(error_msg)  # 已含 agent 名
            status = "error" if result is None else result.status
            code = _classify_code(error_msg, exc)
            reason = _error_reason(error_msg, exc)
            await emit(
                {
                    "type": "agent_end",
                    "agent": name,
                    "step": step.id,
                    "latency_ms": latency_ms,
                    "status": status,
                    "error": error_msg,
                    "reason": reason,
                    "detail": {"reason": reason, "error": error_msg},
                }
            )
            await emit({"type": "error", "code": code, "message": error_msg, "reason": reason})

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
            # 终态错误文案补 latency / 重试次数，供 update_task(error=...) 与前端轮询
            retry_note = f"已重试 {attempt} 次" if attempt > 1 else "首次执行即失败"
            if 0 <= err_idx < len(state.errors):
                state.errors[err_idx] = f"{error_msg}（{retry_note}，耗时 {latency_ms}ms）"
            self._fail_final(state)
            return

    async def _succeed(
        self,
        state: TaskState,
        emit: EmitFn,
        step: PlanStep,
        steps: list[PlanStep],
        done: dict[str, str],
        result: AgentResult,
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
        """必经步骤重试耗尽：进入 failed_final 终态（并发下幂等）。

        用 force_fail_final 走合法迁移链，避免 routing/validating 阶段
        非法迁移导致状态卡死。
        """
        force_fail_final(state)

    async def _finish(self, state: TaskState, emit: EmitFn, t0: float) -> TaskState:
        """写入 final_result、迁移到 completed 并广播 final 事件。"""
        final = self._assemble_final(state)
        if isinstance(final, dict):
            final.setdefault("elapsed_ms", int((time.perf_counter() - t0) * 1000))
        try:
            state.transition(TaskStatus.COMPLETED)
        except ValueError:
            # 竞态：看门狗/兄弟步骤已 force_fail_final → 保持失败终态，幂等返回
            if state.status is TaskStatus.FAILED_FINAL:
                logger.warning("COMPLETED 迁移被跳过 task=%s status=%s", state.task_id, state.status)
                if state.final_result is None:
                    state.final_result = final
                return state
            raise
        state.final_result = final
        await emit({"type": "final", "result": final})
        return state

    @staticmethod
    def _assemble_final(state: TaskState) -> dict:
        """匹配链路用 §2.1 匹配形态，否则沿用数据形态；始终返回 dict 保证 final_result 非空。"""
        match = state.results.get("match_agent")
        if isinstance(match, dict) and match:
            resume = match.get("resume") or {}
            # experience_years 以 profile 为唯一数据源（match_agent._profile_experience_years 同源写入）；
            # 顶层与 resume 冗余一致，避免 final_result 与 profile 矛盾
            experience_years = match.get("experience_years")
            if experience_years is None:
                experience_years = resume.get("experience_years")
            return {
                "task_id": state.task_id,
                "query": state.query,
                "engine": "multi_agent",
                "resume": resume,
                "jobs": match.get("jobs") or [],
                "score": match.get("score"),
                "dimensions": match.get("dimensions") or {},
                "skill_gap": match.get("skill_gap") or [],
                "interpretation": match.get("interpretation") or "",
                "interpretation_source": match.get("interpretation_source") or "mock",
                "experience_years": experience_years,
            }
        data = state.results.get("data_agent") or {}
        final = data.get("final") if isinstance(data, dict) else None
        if not isinstance(final, dict):
            final = dict(data) if isinstance(data, dict) and data else {}
        final.setdefault("task_id", state.task_id)
        final.setdefault("query", state.query)
        return final
