"""BaseAgent / AgentResult / EmitFn：所有 agent 的统一抽象（契约 §3.3）。"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.agent_runtime.state import TaskState
from app.core.logging import get_logger

logger = get_logger(__name__)

# agent 通过 await emit(event_dict) 发 SSE 事件，runtime 注入
EmitFn = Callable[[dict], Awaitable[None]]


async def safe_emit(emit: EmitFn | None, event: dict) -> None:
    """emit 容错：兼容同步/异步 emit，异常只记日志不阻断主链路。"""
    try:
        out = emit(event)  # type: ignore[operator]
        if asyncio.iscoroutine(out):
            await out
    except Exception as exc:
        logger.warning("safe_emit 失败 type=%s: %s", event.get("type"), exc)


async def get_setting_safe(key: str, default: str) -> str:
    """读设置中心（异步，不阻塞事件循环）；模块不存在/DB 不可用时回退默认值。"""
    try:
        from app.core.app_settings import get_setting_async

        return await get_setting_async(key, default)
    except Exception as exc:  # noqa: BLE001 - 降级原则
        logger.warning("app_settings 不可用，使用默认值 %s=%s: %s", key, default, exc)
        return default


@dataclass
class AgentResult:
    """agent 单步执行结果。"""

    status: str                 # "ok" | "error"
    message_type: str           # 如 "data_result"
    data: dict
    next_action: str | None = None
    errors: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """agent 基类：子类实现 run()，经 registry 注册后由 supervisor 调度。"""

    name: str

    @abstractmethod
    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        """执行本 agent 的职责，过程事件通过 emit 发出。"""
