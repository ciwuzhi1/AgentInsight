"""AgentRegistry：进程内 agent 注册表（契约 §3.4）。"""
from __future__ import annotations

from app.agents.base import BaseAgent


class AgentRegistry:
    """按名字注册/查找 agent；未注册抛 KeyError。"""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """注册 agent（同名覆盖）。"""
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent:
        """按名字取 agent；未注册抛 KeyError（code=AGENT_NOT_FOUND）。"""
        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(f"agent 未注册: {name}") from None

    def names(self) -> list[str]:
        """返回全部已注册 agent 名。"""
        return list(self._agents)


# 进程内单例，main.py 启动时向它注册各 agent
registry = AgentRegistry()
