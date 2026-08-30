"""匹配任务 API：resume_id + job_ids 发起多 Agent 匹配（CONTRACTS2 §4.6）。

复用 api/agent 的 TaskBus 与 _run 后台执行流程（不改 agent.py 本身），
事件/SSE/落库链路与 /api/tasks 完全一致。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent_runtime.state import TaskState
from app.api.agent import _matches_lock_key, _run, bus, register_task_lock, register_task_owner
from app.api.auth import UserCtx, get_current_user
from app.cache.redis import acquire_lock
from app.core.logging import get_logger
from app.persistence.mysql import get_jobs_by_ids, get_resume, insert_task

router = APIRouter(prefix="/api/matches", tags=["matches"])
logger = get_logger(__name__)


class MatchCreateRequest(BaseModel):
    resume_id: str
    job_ids: list[int]


@router.post("")
async def create_match(
    body: MatchCreateRequest, user: UserCtx = Depends(get_current_user)
) -> dict:
    """创建匹配任务：校验 resume/jobs 后交 Supervisor 多 Agent 执行（登录必须，归属当前用户）。"""
    if not body.job_ids:
        raise HTTPException(status_code=400, detail="job_ids 不能为空")

    resume = await asyncio.to_thread(get_resume, body.resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail=f"简历不存在: {body.resume_id}")
    # 数据隔离（CONTRACTS3 §3.4）：公共遗留(user_id=NULL)或本人简历才可用
    if resume.get("user_id") is not None and resume["user_id"] != user.user_id:
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

    # 幂等锁（CONTRACTS3 §1.5）：SET NX EX 300；重复提交执行中任务 → 409 + 既有 task_id
    lock_key = _matches_lock_key(body.resume_id, body.job_ids)
    acquired, existing = await acquire_lock(lock_key, state.task_id)
    if not acquired:
        return JSONResponse(
            status_code=409,
            content={"detail": "相同任务正在执行中", "task_id": existing},
        )
    register_task_lock(state.task_id, lock_key)
    register_task_owner(state.task_id, user.user_id)

    try:
        await asyncio.to_thread(
            insert_task, state.task_id, None, state.query, "created", user.user_id
        )
    except Exception as exc:
        # 落库失败不阻断任务，只告警
        logger.warning("匹配任务落库失败 task=%s: %s", state.task_id, exc)

    # 与 /api/tasks 同一 TaskBus；句柄存 Bus 防 GC，兜底拉起清扫任务
    task = asyncio.create_task(_run(state))
    bus.attach(state.task_id, task)
    bus.start_sweeper()
    return {"task_id": state.task_id, "status": "created"}
