"""BaseAgent / AgentResult / EmitFn：所有 agent 的统一抽象（契约 §3.3）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.agent_runtime.state import TaskState

# agent 通过 await emit(event_dict) 发 SSE 事件，runtime 注入
EmitFn = Callable[[dict], Awaitable[None]]


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
