"""AgentMessage：agent 间消息信封（契约 §3.2）。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class AgentMessage:
    """一次 agent 间通信的完整记录。"""

    message_id: str      # uuid4
    task_id: str
    sender: str          # agent 名，supervisor 用 "supervisor"
    receiver: str | list[str]  # "runtime" 或下一 agent 名（可为列表）
    type: str            # 如 "data_result" / "validation_result" / "error"
    payload: dict
    created_at: str


def make_message(
    task_id: str,
    sender: str,
    receiver: str | list[str],
    type: str,
    payload: dict,
) -> AgentMessage:
    """便捷工厂：自动填 message_id / created_at。"""
    return AgentMessage(
        message_id=str(uuid.uuid4()),
        task_id=task_id,
        sender=sender,
        receiver=receiver,
        type=type,
        payload=payload,
        created_at=datetime.now().isoformat(),
    )
