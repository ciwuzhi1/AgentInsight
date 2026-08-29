"""AgentRegistry：进程内 agent 注册表（契约 §3.4）。"""
from __future__ import annotations

from app.agents.base import BaseAgent


class AgentRegistry:
    """按名字注册/查找 agent；未注册抛 KeyError。"""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent:
        try:
            return self._agents[name]
        except KeyError:
            raise KeyError(f"agent not registered: {name}") from None

    def names(self) -> list[str]:
        return list(self._agents)


# 进程内单例，main.py 启动时向它注册各 agent
registry = AgentRegistry()
