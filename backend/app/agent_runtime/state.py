"""TaskState / TaskStatus：任务运行时状态机（契约 §3.1）。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4


class TaskStatus(str, Enum):
    """任务状态；completed / failed_final 为终态。"""

    CREATED = "created"
    ROUTING = "routing"
    RUNNING = "running"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    FAILED_FINAL = "failed_final"


# 合法迁移表。除契约列出的主链路外，补充 running→failed 与
# failed→failed_final：supervisor 单步异常时需先落 failed，重试耗尽后
# 从 failed 直接进终态 failed_final（契约 §3.5 流程隐含要求）。
_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.ROUTING},
    TaskStatus.ROUTING: {TaskStatus.RUNNING, TaskStatus.VALIDATING},
    TaskStatus.RUNNING: {TaskStatus.VALIDATING, TaskStatus.FAILED},
    TaskStatus.VALIDATING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.RETRYING, TaskStatus.FAILED_FINAL},
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.FAILED_FINAL},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED_FINAL: set(),
}


class TaskState:
    """一次分析任务的完整运行时状态。"""

    def __init__(self, dataset_id: str | None = None, query: str = "") -> None:
        now = datetime.now().isoformat()
        self.task_id: str = str(uuid4())
        self.dataset_id: str | None = dataset_id
        self.query: str = query
        self.status: TaskStatus = TaskStatus.CREATED
        self.current_agents: set[str] = set()  # 并行波次下多 agent 同时在跑
        self.current_step: int = 0
        self.plan: list[str] = []
        self.plan_steps: list = []   # PlanStep 列表（planner 产出），plan 可由此派生
        self.messages: list = []     # AgentMessage 列表（executor 追加）
        self.context: dict = {}
        self.results: dict = {}
        self.errors: list[str] = []
        self.retry_count: int = 0
        self.final_result: dict | None = None
        self.created_at: str = now
        self.updated_at: str = now

    def transition(self, to: TaskStatus) -> None:
        """状态迁移；非法迁移或从终态迁出抛 ValueError。"""
        allowed = _TRANSITIONS[self.status]
        if to not in allowed:
            raise ValueError(
                f"illegal transition: {self.status.value} -> {to.value}"
            )
        self.status = to
        self.updated_at = datetime.now().isoformat()
