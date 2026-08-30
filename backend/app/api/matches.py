"""匹配任务 API：resume_id + job_ids 发起多 Agent 匹配（CONTRACTS2 §4.6）。

复用 api/agent 的 TaskBus 与 _run 后台执行流程（不改 agent.py 本身），
事件/SSE/落库链路与 /api/tasks 完全一致。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent_runtime.state import TaskState
from app.api.agent import _run, bus
from app.core.logging import get_logger
from app.persistence.mysql import get_jobs_by_ids, get_resume, insert_task

router = APIRouter(prefix="/api/matches", tags=["matches"])
logger = get_logger(__name__)


class MatchCreateRequest(BaseModel):
    resume_id: str
    job_ids: list[int]


@router.post("")
async def create_match(body: MatchCreateRequest) -> dict:
    """创建匹配任务：校验 resume/jobs 后交 Supervisor 多 Agent 执行。"""
    if not body.job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")

    resume = await asyncio.to_thread(get_resume, body.resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"简历不存在: {body.resume_id}")

    job_ids = list(dict.fromkeys(body.job_ids))  # 去重保序
    jobs = await asyncio.to_thread(get_jobs_by_ids, job_ids)
    if len(jobs) != len(job_ids):
        raise HTTPException(status_code=400, detail="部分岗位不存在，请重新选择")
    if not jobs:
        raise HTTPException(status_code=400, detail="岗位查询结果为空")

    state = TaskState(query=f"简历与 {len(job_ids)} 个岗位匹配分析")
    state.context = {
        "resume": {
            "resume_id": resume["id"],
            "filename": resume["filename"],
            "path": resume["path"],
        },
        "jobs": jobs,
        "query": state.query,
    }

    try:
        await asyncio.to_thread(insert_task, state.task_id, None, state.query)
    except Exception as exc:
        # 落库失败不阻断任务，只告警
        logger.warning("匹配任务落库失败 task=%s: %s", state.task_id, exc)

    # 与 /api/tasks 同一 TaskBus；句柄存 Bus 防 GC，兜底拉起清扫任务
    task = asyncio.create_task(_run(state))
    bus.attach(state.task_id, task)
    bus.start_sweeper()
    return {"task_id": state.task_id, "status": "created"}
