"""任务 API：创建任务、SSE 事件流、任务详情（契约 §7/§8；健壮性见 CONTRACTS2 §5）。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent_runtime.registry import registry
from app.agent_runtime.state import TaskState
from app.agent_runtime.supervisor import Supervisor
from app.api.datasets import table_name_for
from app.core.logging import get_logger
from app.persistence.mysql import (
    get_dataset,
    insert_task,
    insert_task_step,
    update_task,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
logger = get_logger(__name__)


# ---------- TaskBus：进程内事件总线（内存回放 + Condition 等待） ----------

# 每任务事件上限（drop-oldest）；完结任务 TTL 与清扫间隔
_EVENT_CAP = 500
_FINISHED_TTL_S = 30 * 60
_SWEEP_INTERVAL_S = 10 * 60


class TaskBus:
    """按 task_id 缓存事件，支持 SSE 订阅者先回放再等新事件。

    每个任务一条记录：{"events", "cond", "task", "finished_at", "truncated"}。
    健壮性（CONTRACTS2 §5）：事件数上限 _EVENT_CAP 超限淘汰最旧并标注
    {"type":"truncated"}；完结任务 TTL 到期由模块级后台清扫任务整体清理。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, dict] = {}
        self._sweeper: asyncio.Task | None = None

    def _entry(self, task_id: str) -> dict:
        if task_id not in self._tasks:
            self._tasks[task_id] = {
                "events": [],
                "cond": asyncio.Condition(),
                "task": None,       # asyncio.Task 句柄，防 GC
                "finished_at": None,  # time.monotonic() 终态时间戳
                "truncated": False,
            }
        return self._tasks[task_id]

    def exists(self, task_id: str) -> bool:
        return task_id in self._tasks

    def snapshot(self, task_id: str) -> list[dict]:
        """当前已发布事件的浅拷贝（供详情接口回放）；被截断时头部补标注。"""
        entry = self._tasks.get(task_id, {})
        events = list(entry.get("events", []))
        if entry.get("truncated"):
            events.insert(0, {"type": "truncated"})
        return events

    def attach(self, task_id: str, task: "asyncio.Task") -> None:
        """把 _run 的 create_task 句柄存进 Bus 防 GC；完结时打终态时间戳。"""
        entry = self._entry(task_id)
        entry["task"] = task
        task.add_done_callback(lambda _t: self._mark_finished(task_id))

    def _mark_finished(self, task_id: str) -> None:
        entry = self._tasks.get(task_id)
        if entry is None:
            return
        # 终态 Task 的句柄不再持有（释放 result/帧引用）
        entry["task"] = None
        if entry["finished_at"] is None:
            entry["finished_at"] = time.monotonic()

    async def publish(self, task_id: str, event: dict) -> None:
        entry = self._entry(task_id)
        async with entry["cond"]:
            events = entry["events"]
            events.append(event)
            while len(events) > _EVENT_CAP:  # drop-oldest
                events.pop(0)
                entry["truncated"] = True
            if event.get("type") in ("final", "error"):
                self._mark_finished(task_id)
            entry["cond"].notify_all()

    def start_sweeper(self) -> None:
        """拉起 TTL 清扫后台任务（幂等，仅在有事件循环时创建）。"""
        if self._sweeper is not None and not self._sweeper.done():
            return
        self._sweeper = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_S)
            try:
                self._sweep()
            except Exception:
                logger.exception("TaskBus 清扫异常")

    def _sweep(self) -> None:
        """清理 TTL 到期的完结任务（events/cond/Task 引用一起清）。"""
        now = time.monotonic()
        for task_id in list(self._tasks):
            entry = self._tasks[task_id]
            finished_at = entry["finished_at"]
            if finished_at is not None and now - finished_at >= _FINISHED_TTL_S:
                del self._tasks[task_id]
                _STEP_SEQS.pop(task_id, None)

    async def subscribe(self, task_id: str) -> AsyncIterator[dict]:
        """先回放既有事件（截断时头部补 {"type":"truncated"}），再等新事件；
        final / error 终止。"""
        entry = self._entry(task_id)
        if entry["truncated"]:
            yield {"type": "truncated"}
        idx = 0
        while True:
            async with entry["cond"]:
                while idx >= len(entry["events"]):
                    await entry["cond"].wait()
                batch = entry["events"][idx:]
                idx = len(entry["events"])
            for event in batch:
                yield event
                if event.get("type") in ("final", "error"):
                    return


bus = TaskBus()

# task_id -> {step_id: 序号}；事件里的 step 是字符串 id，task_steps.step 是 INT 列
_STEP_SEQS: dict[str, dict[str, int]] = {}


def _step_seq(task_id: str, step_id: str) -> int:
    """step id 首次出现时按顺序分配序号（同一任务内自增）。"""
    seq = _STEP_SEQS.setdefault(task_id, {})
    if step_id not in seq:
        seq[step_id] = len(seq) + 1
    return seq[step_id]


# ---------- 模型与工具 ----------

class TaskCreateRequest(BaseModel):
    dataset_id: str
    query: str


def _register_agents() -> None:
    """确保全部 Agent 已注册（幂等；并行模块延迟导入）。"""
    try:
        registry.get("data_agent")
    except KeyError:
        from app.agents.data_agent import DataAgent

        registry.register(DataAgent())
    try:
        registry.get("validator_agent")
    except KeyError:
        from app.agents.validator_agent import ValidatorAgent

        registry.register(ValidatorAgent())
    try:
        registry.get("resume_agent")
    except KeyError:
        from app.agents.resume_agent import ResumeAgent

        registry.register(ResumeAgent())
    try:
        registry.get("job_agent")
    except KeyError:
        from app.agents.job_agent import JobAgent

        registry.register(JobAgent())
    try:
        registry.get("match_agent")
    except KeyError:
        from app.agents.match_agent import MatchAgent

        registry.register(MatchAgent())


# ---------- 路由 ----------

@router.post("")
async def create_task(body: TaskCreateRequest) -> dict:
    """创建任务并后台启动 run_task。"""
    try:
        ds = await asyncio.to_thread(get_dataset, body.dataset_id)
    except Exception as exc:
        logger.warning("数据集查询失败 dataset=%s: %s", body.dataset_id, exc)
        ds = None
    if not ds:
        raise HTTPException(status_code=404, detail=f"数据集不存在: {body.dataset_id}")

    state = TaskState(dataset_id=body.dataset_id, query=body.query)
    state.context["dataset"] = {
        "dataset_id": ds["id"],
        "name": ds["name"],
        "path": ds["path"],
        "table_name": table_name_for(ds["id"]),
        "schema": ds.get("schema_json") or [],
    }

    try:
        await asyncio.to_thread(insert_task, state.task_id, state.dataset_id, state.query)
    except Exception as exc:
        # 落库失败不阻断分析，只告警
        logger.warning("任务落库失败 task=%s: %s", state.task_id, exc)

    # 句柄存进 Bus 防 GC；兜底拉起清扫任务（幂等）
    task = asyncio.create_task(_run(state))
    bus.attach(state.task_id, task)
    bus.start_sweeper()
    return {"task_id": state.task_id, "status": "created"}


async def _run(state: TaskState) -> None:
    """后台执行：emit 桥接 SSE 与落库，再交给 Supervisor 编排。"""

    async def emit(event: dict) -> None:
        await bus.publish(state.task_id, event)
        try:
            await _persist_event(state, event)
        except Exception as exc:
            logger.warning("事件落库失败 task=%s event=%s: %s", state.task_id, event.get("type"), exc)

    try:
        _register_agents()
        await Supervisor().run_task(state, emit)
    except Exception as exc:
        logger.exception("任务执行异常 task=%s", state.task_id)
        try:
            await emit({"type": "error", "code": "ENGINE_ERROR", "message": str(exc)})
        except Exception:
            pass


async def _persist_event(state: TaskState, event: dict) -> None:
    """按事件类型同步落库（同步 DB 调用已 to_thread）。

    step 适配：事件 step 是字符串 id，task_steps.step 是 INT 列——
    任务内首次出现的 step id 按顺序分配序号，原始 id 写进 detail JSON。
    plan / retry / step_skipped 事件不落 task_steps（自然落空）。
    """
    etype = event.get("type")
    if etype == "state":
        await asyncio.to_thread(update_task, state.task_id, event["status"])
    elif etype == "agent_start":
        step_id = str(event.get("step") or state.current_step or "")
        await asyncio.to_thread(
            insert_task_step,
            state.task_id,
            _step_seq(state.task_id, step_id),
            event["agent"],
            "running",
            retry_count=state.retry_count,
            detail={"step_id": step_id},
        )
    elif etype == "agent_end":
        step_id = str(event.get("step") or "")
        detail = dict(event.get("detail") or {})
        detail["step_id"] = step_id
        await asyncio.to_thread(
            insert_task_step,
            state.task_id,
            _step_seq(state.task_id, step_id),
            event["agent"],
            event.get("status", "ok"),
            latency_ms=event.get("latency_ms"),
            retry_count=state.retry_count,
            detail=detail,
        )
    elif etype == "error":
        await asyncio.to_thread(
            update_task, state.task_id, "failed_final", error=event.get("message")
        )
    elif etype == "final":
        result = event.get("result") or {}
        await asyncio.to_thread(
            update_task,
            state.task_id,
            "completed",
            engine=result.get("engine"),
            final_result=result,
        )
        # 匹配链路钩子（CONTRACTS2 §4.6）：final 含 score 时落 matches
        if isinstance(result, dict) and "score" in result:
            await _insert_match(state.task_id, result)


async def _insert_match(task_id: str, result: dict) -> None:
    """final 事件含 score 时调用 insert_match（A4 交付，缺函数仅告警不崩）。"""
    try:
        from app.persistence.mysql import insert_match
    except ImportError:
        logger.warning("insert_match 尚未交付，跳过匹配结果落库 task=%s", task_id)
        return
    resume = result.get("resume") or {}
    job_ids = [j.get("id") for j in (result.get("jobs") or []) if isinstance(j, dict)]
    try:
        await asyncio.to_thread(
            insert_match,
            task_id,
            resume.get("resume_id"),
            job_ids,
            result.get("score"),
            result,
        )
    except Exception as exc:
        logger.warning("匹配结果落库失败 task=%s: %s", task_id, exc)


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    """从 Bus 事件回放拼装任务详情。"""
    if not bus.exists(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    status = "created"
    engine: str | None = None
    final_result: dict | None = None
    steps: list[dict] = []
    for event in bus.snapshot(task_id):
        etype = event.get("type")
        if etype == "state":
            status = event.get("status", status)
        elif etype == "agent_end":
            steps.append(
                {
                    "agent_name": event.get("agent"),
                    "status": event.get("status"),
                    "latency_ms": event.get("latency_ms"),
                }
            )
        elif etype == "error":
            status = "failed_final"
        elif etype == "final":
            final_result = event.get("result")
            status = "completed"
            engine = (final_result or {}).get("engine")
    return {
        "task_id": task_id,
        "status": status,
        "engine": engine,
        "final_result": final_result,
        "steps": steps,
    }


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request) -> StreamingResponse:
    """SSE 事件流：每事件 data: {json}\\n\\n，自然结束补 event: done。"""
    if not bus.exists(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in bus.subscribe(task_id):
                if await request.is_disconnected():
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
