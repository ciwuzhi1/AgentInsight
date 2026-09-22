"""任务 API：创建任务、SSE 事件流、任务详情（契约 §7/§8；健壮性见 CONTRACTS2 §5）。"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.agent_runtime.executor import force_fail_final
from app.agent_runtime.registry import registry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agent_runtime.supervisor import Supervisor
from app.api.auth import UserCtx, get_current_user, get_current_user_flex
from app.api.datasets import table_name_for
from app.cache.keys import text_hash
from app.cache.redis import acquire_lock, release_lock
from app.core.logging import get_logger
from app.core.rate_limit import task_limiter
from app.persistence.mysql import (
    get_dataset,
    get_task_row as mysql_get_task_row,
    get_task_steps as mysql_get_task_steps,
    insert_task,
    insert_task_step,
    list_tasks as mysql_list_tasks,
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
            if event.get("type") == "final" or (
                event.get("type") == "error" and event.get("terminal")
            ):
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
        final / terminal error 终止（非终态 error 如重试中不终止）。"""
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
                # 只在 final 或 terminal error 时终止；重试中的 error 不终止
                if event.get("type") == "final" or (
                    event.get("type") == "error" and event.get("terminal")
                ):
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


# ---------- 任务幂等锁（CONTRACTS3 §1.5） ----------

# SET NX EX 300：执行中任务重复提交 → 409 + 既有 task_id
_LOCK_TTL_S = 300
# task_id -> lock key；_run 终结时统一 DEL（含异常/取消路径）
_PENDING_LOCKS: dict[str, str] = {}


def _tasks_lock_key(body: "TaskCreateRequest") -> str:
    """锁 key：请求体内容（dataset_id/query）哈希。"""
    return f"agent:lock:{text_hash(f'dataset_id={body.dataset_id}', f'query={body.query}')}"


def _matches_lock_key(resume_id: str, job_ids: list[int]) -> str:
    """锁 key：resume_id + 排序去重后的 job_ids 哈希。"""
    ids = ",".join(str(i) for i in sorted(set(job_ids)))
    return f"agent:lock:{text_hash(f'resume_id={resume_id}', f'job_ids={ids}')}"


def register_task_lock(task_id: str, lock_key: str) -> None:
    """登记幂等锁（/api/tasks 与 /api/matches 两入口共用）；_run 终结时 DEL。"""
    _PENDING_LOCKS[task_id] = lock_key


# ---------- 任务属主（CONTRACTS3 §3.4） ----------

# task_id -> owner user_id（None=公共遗留/旧任务，任何登录用户可见）。
# Bus 与属主表均为进程内存：get_task / SSE 订阅先验 bus.exists，故重启后自然 404，
# 无需回源 DB。
_TASK_OWNERS: dict[str, str | None] = {}


def register_task_owner(task_id: str, user_id: str | None) -> None:
    """登记任务属主（/api/tasks 与 /api/matches 两入口共用）。"""
    _TASK_OWNERS[task_id] = user_id


def ensure_task_visible(task_id: str, user: UserCtx) -> None:
    """读路径隔离：owner ∈ {None, 当前用户} 才可见，否则 404（不泄露存在性）。"""
    owner = _TASK_OWNERS.get(task_id)
    if owner is not None and owner != user.user_id:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")


# ---------- 模型与工具 ----------

class TaskCreateRequest(BaseModel):
    dataset_id: str
    query: str = Field(min_length=1, max_length=2000)


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
    try:
        registry.get("report_synthesizer")
    except KeyError:
        from app.agents.report_agent import ReportSynthesizer

        registry.register(ReportSynthesizer())


# ---------- 错误文案（可观测契约：tasks.error 必须非空且可读） ----------

def _step_error_text(event: dict) -> str:
    """步骤失败落库文案：非空，含 agent 名、latency 与 reason 摘要。"""
    agent = event.get("agent") or "unknown_agent"
    latency = event.get("latency_ms")
    reason = (event.get("error") or event.get("message") or "").strip() or "步骤执行失败"
    reason_code = (event.get("reason") or "").strip()
    suffix = f"（reason={reason_code}）" if reason_code else ""
    if latency is not None:
        return f"{agent} 执行失败（耗时 {latency}ms）{suffix}：{reason}"
    return f"{agent} 执行失败{suffix}：{reason}"


def _task_error_text(state: TaskState, event: dict | None = None) -> str:
    """任务级 tasks.error 文案：优先事件 message，否则 state.errors，兜底非空且含 agent。"""
    msg = ""
    agent = ""
    if event is not None:
        msg = (event.get("message") or "").strip()
        agent = (event.get("agent") or "").strip()
    if not msg and state.errors:
        msg = state.errors[-1].strip()
    if not msg:
        agents = "、".join(sorted(state.current_agents)) or "unknown_agent"
        return f"{agents} 任务执行失败"
    # 可观测契约：error 需能定位 agent；文案未含 agent 时补前缀
    if agent and agent not in msg:
        return f"{agent}：{msg}"
    if not agent and state.current_agents:
        for name in sorted(state.current_agents):
            if name and name not in msg:
                return f"{name}：{msg}"
    return msg


def _current_status_str(state: TaskState) -> str:
    """TaskState.status → 落库字符串（Enum 兼容）。"""
    status = state.status
    return status.value if hasattr(status, "value") else str(status)


# ---------- 路由 ----------

@router.post("")
async def create_task(body: TaskCreateRequest, user: UserCtx = Depends(get_current_user)) -> dict:
    """创建任务并后台启动 run_task（登录必须；限流 30 次/分钟/用户）。"""
    ok, wait = task_limiter.allow(f"task:{user.user_id}")
    if not ok:
        raise HTTPException(status_code=429, detail=f"任务创建过于频繁，请 {wait}s 后重试")
    try:
        ds = await asyncio.to_thread(get_dataset, body.dataset_id)
    except Exception as exc:
        logger.warning("数据集查询失败 dataset=%s: %s", body.dataset_id, exc)
        ds = None
    if not ds:
        raise HTTPException(status_code=404, detail=f"数据集不存在: {body.dataset_id}")
    # 数据隔离（CONTRACTS3 §3.4）：公共遗留(user_id=NULL)或本人数据才可用
    if ds.get("user_id") is not None and ds["user_id"] != user.user_id:
        raise HTTPException(status_code=404, detail=f"数据集不存在: {body.dataset_id}")

    state = TaskState(dataset_id=body.dataset_id, query=body.query)
    state.context["dataset"] = {
        "dataset_id": ds["id"],
        "name": ds["name"],
        "path": ds["path"],
        "table_name": table_name_for(ds["id"]),
        "schema": ds.get("schema_json") or [],
    }

    # 幂等锁：SET NX EX 300；未获得（重复提交执行中任务）→ 409 + 既有 task_id。
    # Redis 不可用 → acquire_lock 降级放行，照常创建。
    lock_key = _tasks_lock_key(body)
    acquired, existing = await acquire_lock(lock_key, state.task_id, _LOCK_TTL_S)
    if not acquired:
        return JSONResponse(
            status_code=409,
            content={"detail": "相同任务正在执行中", "task_id": existing},
        )
    register_task_lock(state.task_id, lock_key)
    register_task_owner(state.task_id, user.user_id)

    try:
        await asyncio.to_thread(
            insert_task, state.task_id, state.dataset_id, state.query, "created", user.user_id
        )
    except Exception as exc:
        # 落库失败不阻断分析，只告警
        logger.warning("任务落库失败 task=%s: %s", state.task_id, exc)

    # 句柄存进 Bus 防 GC；兜底拉起清扫任务（幂等）
    task = asyncio.create_task(_run(state))
    bus.attach(state.task_id, task)
    bus.start_sweeper()
    return {"task_id": state.task_id, "status": "created"}


async def _run(state: TaskState) -> None:
    """后台执行：emit 桥接 SSE 与落库，再交给 Supervisor 编排。

    幂等锁在此终结：finally 覆盖 completed / failed_final / 异常 / 取消全部路径。
    """
    lock_key = _PENDING_LOCKS.pop(state.task_id, None)

    async def emit(event: dict) -> None:
        await bus.publish(state.task_id, event)
        try:
            await _persist_event(state, event)
        except Exception as exc:
            logger.warning("事件落库失败 task=%s event=%s: %s", state.task_id, event.get("type"), exc)

    try:
        _register_agents()
        await Supervisor().run_task(state, emit)
        # 任务终态失败时补发 terminal error 事件（executor/supervisor 不单独 emit）
        if state.status is TaskStatus.FAILED_FINAL:
            last_err = _task_error_text(state)
            code = state.context.get("error_code") or "ENGINE_ERROR"
            reason = state.context.get("error_reason") or ""
            event: dict = {
                "type": "error",
                "code": code,
                "message": last_err,
                "terminal": True,
            }
            if reason:
                event["reason"] = reason
            await emit(event)
            # 兜底：failed_final + 非空 error 必达 DB（防 _persist_event 被跳过）
            try:
                await asyncio.to_thread(
                    update_task, state.task_id, "failed_final", error=last_err
                )
            except Exception as persist_exc:
                logger.warning(
                    "终态 tasks.error 落库失败 task=%s: %s",
                    state.task_id,
                    persist_exc,
                )
    except Exception as exc:
        logger.exception("任务执行异常 task=%s", state.task_id)
        try:
            # 确保状态进入终态再发 terminal error（force_fail_final 走合法迁移链，
            # 覆盖 routing/validating 等非 RUNNING 阶段的异常，避免永远 routing）
            force_fail_final(state)
            raw = (str(exc) or "").strip() or type(exc).__name__
            state.errors.append(f"任务执行异常：{raw}")
            state.context.setdefault("error_code", "ENGINE_ERROR")
            state.context.setdefault("error_reason", "unhandled_exception")
            err_msg = _task_error_text(state)
            await emit(
                {
                    "type": "error",
                    "code": state.context["error_code"],
                    "message": err_msg,
                    "reason": state.context["error_reason"],
                    "terminal": True,
                }
            )
            # 兜底：异常路径 DB 记 failed_final + 非空 error，便于前端轮询
            try:
                await asyncio.to_thread(
                    update_task, state.task_id, "failed_final", error=err_msg
                )
            except Exception as persist_exc:
                logger.warning(
                    "异常路径 tasks.error 落库失败 task=%s: %s",
                    state.task_id,
                    persist_exc,
                )
        except Exception:
            pass
    finally:
        # 任务终结（completed/failed_final/异常/取消）统一 DEL 幂等锁
        if lock_key:
            await release_lock(lock_key)


async def _persist_event(state: TaskState, event: dict) -> None:
    """按事件类型同步落库（同步 DB 调用已 to_thread）。

    step 适配：事件 step 是字符串 id，task_steps.step 是 INT 列——
    任务内首次出现的 step id 按顺序分配序号，原始 id 写进 detail JSON。
    plan / retry / step_skipped 事件不落 task_steps（自然落空）。

    错误可观测：步骤 error / 任务 failed_final 时 update_task 必须写
    非空 error 文案（含 agent 名与 latency）；steps.detail 带 reason 摘要；
    completed 的 final_result 结构保持不变。
    """
    etype = event.get("type")
    if etype == "state":
        status = event["status"]
        if status == "failed_final":
            await asyncio.to_thread(
                update_task,
                state.task_id,
                status,
                error=_task_error_text(state, event),
            )
        else:
            await asyncio.to_thread(update_task, state.task_id, status)
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
        step_status = event.get("status", "ok")
        if step_status not in ("ok", "skipped"):
            detail["error"] = (
                event.get("error") or detail.get("error") or "步骤执行失败"
            )
            # reason 摘要：timeout / llm_timeout / llm_error / sql_error 等
            reason = (event.get("reason") or detail.get("reason") or "").strip()
            if reason:
                detail["reason"] = reason
        await asyncio.to_thread(
            insert_task_step,
            state.task_id,
            _step_seq(state.task_id, step_id),
            event["agent"],
            step_status,
            latency_ms=event.get("latency_ms"),
            retry_count=state.retry_count,
            detail=detail,
        )
        # 步骤失败 → 同步 tasks.error（非空，含 agent + latency + reason）
        if step_status not in ("ok", "skipped"):
            await asyncio.to_thread(
                update_task,
                state.task_id,
                _current_status_str(state),
                error=_step_error_text(event),
            )
    elif etype == "error":
        # 终态或已是 failed_final 时写 tasks.error；文案保证非空且含 agent
        if state.status is TaskStatus.FAILED_FINAL or event.get("terminal"):
            await asyncio.to_thread(
                update_task,
                state.task_id,
                "failed_final",
                error=_task_error_text(state, event),
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
            _TASK_OWNERS.get(task_id),  # 属主（None=公共遗留）
        )
    except Exception as exc:
        logger.warning("匹配结果落库失败 task=%s: %s", task_id, exc)


@router.get("")
async def list_tasks(
    limit: int = 20, page: int = 1, user: UserCtx = Depends(get_current_user)
) -> dict:
    """任务历史（分页：page 从 1 起；归属隔离，NULL 公共可见）。"""
    limit_n = max(1, min(limit, 100))
    page_n = max(1, page)
    items = await asyncio.to_thread(
        mysql_list_tasks, user.user_id, limit_n, (page_n - 1) * limit_n
    )
    return {"page": page_n, "limit": limit_n, "count": len(items), "items": items,
            "has_more": len(items) == limit_n}


@router.get("/{task_id}/trace")
async def get_task_trace(task_id: str, user: UserCtx = Depends(get_current_user)) -> dict:
    """历史任务 trace（MySQL）：任务行 + 步骤 trace + final_result，供前端回放旧任务。"""
    row = await asyncio.to_thread(mysql_get_task_row, task_id)
    if row is None or (row.get("user_id") is not None and row["user_id"] != user.user_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    steps = await asyncio.to_thread(mysql_get_task_steps, task_id)
    return {
        "task_id": task_id,
        "status": row.get("status"),
        "engine": row.get("engine"),
        "error": row.get("error"),
        "query": row.get("query"),
        "created_at": row.get("created_at"),
        "completed_at": row.get("completed_at"),
        "steps": steps,
        "final_result": row.get("final_result"),
    }


@router.get("/{task_id}/export")
async def export_task(
    task_id: str, format: str = "csv", user: UserCtx = Depends(get_current_user)
) -> Response:
    """导出任务结果：format=csv（columns+rows）或 json（完整 final_result）。"""
    row = await asyncio.to_thread(mysql_get_task_row, task_id)
    if row is None or (row.get("user_id") is not None and row["user_id"] != user.user_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    final = row.get("final_result")
    if not final:
        raise HTTPException(status_code=404, detail="该任务没有可导出的结果")
    safe_name = f"task_{task_id[:8]}"
    if format == "json":
        return Response(
            content=json.dumps(final, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
        )
    columns = final.get("columns") or []
    rows = final.get("rows") or []
    if not columns or not rows:
        raise HTTPException(status_code=404, detail="该任务没有表格数据（匹配任务请导出 JSON）")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
    )


@router.get("/{task_id}")
async def get_task(task_id: str, user: UserCtx = Depends(get_current_user)) -> dict:
    """任务详情（前端轮询）：返回 status/error/final_result/engine。

    Bus 回放优先；Bus 不存在（进程重启/事件已清扫）时回退 MySQL，
    保证轮询始终能看到终态与 error 文案。
    """
    if not bus.exists(task_id):
        try:
            row = await asyncio.to_thread(mysql_get_task_row, task_id)
        except Exception as exc:
            logger.warning("任务详情 MySQL 回退查询失败 task=%s: %s", task_id, exc)
            row = None
        if row is None or (
            row.get("user_id") is not None and row["user_id"] != user.user_id
        ):
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        return {
            "task_id": task_id,
            "status": row.get("status"),
            "engine": row.get("engine"),
            "final_result": row.get("final_result"),
            "error": row.get("error"),
            "steps": [],
        }
    ensure_task_visible(task_id, user)

    status = "created"
    engine: str | None = None
    final_result: dict | None = None
    error: str | None = None
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
                    "error": event.get("error"),
                    "reason": event.get("reason"),
                }
            )
        elif etype == "error":
            # 保留最新 error 文案；terminal 才改 status（重试中的 error 不改状态）
            if event.get("message"):
                error = event.get("message")
            if event.get("terminal"):
                status = "failed_final"
        elif etype == "final":
            final_result = event.get("result")
            status = "completed"
            engine = (final_result or {}).get("engine")
            error = None
    return {
        "task_id": task_id,
        "status": status,
        "engine": engine,
        "final_result": final_result,
        "error": error,
        "steps": steps,
    }


@router.get("/{task_id}/events")
async def task_events(
    task_id: str, request: Request, token: str | None = None, user: UserCtx = Depends(get_current_user_flex)
) -> StreamingResponse:
    """SSE 事件流：每事件 data: {json}\\n\\n，自然结束补 event: done（登录必须 + 属主隔离）。"""
    if not bus.exists(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    ensure_task_visible(task_id, user)

    async def stream() -> AsyncIterator[str]:
        agen = bus.subscribe(task_id)
        try:
            while True:
                # 心跳：空闲 15s 发 SSE 注释行，防代理/浏览器断流；事件到达立即下发
                try:
                    event = await asyncio.wait_for(agen.__anext__(), timeout=15)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": ping\n\n"
                    continue
                except StopAsyncIteration:
                    break
                if await request.is_disconnected():
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            await agen.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
