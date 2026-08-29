"""任务 API：创建任务、SSE 事件流、任务详情（契约 §7/§8）。"""
from __future__ import annotations

import asyncio
import json
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

class TaskBus:
    """按 task_id 缓存全部事件，支持 SSE 订阅者先回放再等新事件。

    每个任务一条记录：{"events": [event...], "cond": asyncio.Condition}。
    publish 追加事件并 notify_all；subscribe 从头回放后阻塞等新事件，
    遇 final / error 事件终止（契约 §7 流程终点）。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, dict] = {}

    def _entry(self, task_id: str) -> dict:
        if task_id not in self._tasks:
            self._tasks[task_id] = {"events": [], "cond": asyncio.Condition()}
        return self._tasks[task_id]

    def exists(self, task_id: str) -> bool:
        return task_id in self._tasks

    def snapshot(self, task_id: str) -> list[dict]:
        """当前已发布事件的浅拷贝（供详情接口回放）。"""
        return list(self._tasks.get(task_id, {}).get("events", []))

    async def publish(self, task_id: str, event: dict) -> None:
        entry = self._entry(task_id)
        async with entry["cond"]:
            entry["events"].append(event)
            entry["cond"].notify_all()

    async def subscribe(self, task_id: str) -> AsyncIterator[dict]:
        """先回放既有事件，再等新事件；final / error 终止。"""
        entry = self._entry(task_id)
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


# ---------- 模型与工具 ----------

class TaskCreateRequest(BaseModel):
    dataset_id: str
    query: str


def _register_agents() -> None:
    """确保 data_agent / validator_agent 已注册（幂等；并行模块延迟导入）。"""
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

    asyncio.create_task(_run(state))
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
    """按事件类型同步落库（同步 DB 调用已 to_thread）。"""
    etype = event.get("type")
    if etype == "state":
        await asyncio.to_thread(update_task, state.task_id, event["status"])
    elif etype == "agent_start":
        await asyncio.to_thread(
            insert_task_step,
            state.task_id,
            event.get("step", state.current_step),
            event["agent"],
            "running",
            retry_count=state.retry_count,
        )
    elif etype == "agent_end":
        await asyncio.to_thread(
            insert_task_step,
            state.task_id,
            state.current_step,
            event["agent"],
            event.get("status", "ok"),
            latency_ms=event.get("latency_ms"),
            retry_count=state.retry_count,
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
