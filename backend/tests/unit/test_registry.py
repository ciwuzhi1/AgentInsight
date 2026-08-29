"""AgentRegistry 单测（契约 §3.4/§14）。"""
import pytest

from app.agent_runtime.registry import AgentRegistry, registry
from app.agent_runtime.state import TaskState
from app.agents.base import AgentResult, BaseAgent


class DummyAgent(BaseAgent):
    """最小可注册 agent，仅用于注册表行为测试。"""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, state: TaskState, emit) -> AgentResult:
        return AgentResult(status="ok", message_type="data_result", data={})


def test_register_get_names():
    r = AgentRegistry()
    a = DummyAgent("data_agent")
    b = DummyAgent("validator_agent")
    r.register(a)
    r.register(b)
    assert r.get("data_agent") is a
    assert r.get("validator_agent") is b
    assert set(r.names()) == {"data_agent", "validator_agent"}


def test_get_unregistered_raises_keyerror():
    r = AgentRegistry()
    with pytest.raises(KeyError):
        r.get("nope")


def test_register_same_name_overwrites():
    r = AgentRegistry()
    a1 = DummyAgent("x")
    a2 = DummyAgent("x")
    r.register(a1)
    r.register(a2)
    assert r.get("x") is a2
    assert r.names() == ["x"]


def test_global_registry_is_singleton_instance():
    """main.py 启动时向该单例注册各 agent。"""
    assert isinstance(registry, AgentRegistry)
    assert isinstance(registry.names(), list)
