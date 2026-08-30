"""executor 单测（CONTRACTS2 §1.3 / §7）：波次并行、重试、optional 跳过、AgentMessage。

DummyAgent 模式参照 test_registry.py；异步用 asyncio.run 驱动，全程离线。
"""
import asyncio

from app.agent_runtime import executor as executor_mod
from app.agent_runtime.executor import WorkflowExecutor
from app.agent_runtime.planner import PlanError, PlanStep
from app.agent_runtime.registry import AgentRegistry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import AgentResult, BaseAgent


def make_running_state(query: str = "分析") -> TaskState:
    """构造已处于 RUNNING 的任务状态（supervisor 进入 executor 前的状态）。"""
    state = TaskState(query=query)
    state.transition(TaskStatus.ROUTING)
    state.transition(TaskStatus.RUNNING)
    return state


def make_registry(*agents) -> AgentRegistry:
    reg = AgentRegistry()
    for a in agents:
        reg.register(a)
    return reg


def event_recorder():
    """返回 (events, emit)：记录 executor 发出的全部事件。"""
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    return events, emit


class ScriptedAgent(BaseAgent):
    """可编排的 dummy：前 N 次调用抛异常，之后返回固定 data。"""

    def __init__(self, name: str, error_first: int = 0, data: dict | None = None,
                 message_type: str = "data_result") -> None:
        self.name = name
        self.calls = 0
        self._error_first = error_first
        self._data = data if data is not None else {"agent": name}
        self._message_type = message_type

    async def run(self, state, emit) -> AgentResult:
        self.calls += 1
        if self.calls <= self._error_first:
            raise RuntimeError(f"{self.name} 第 {self.calls} 次调用失败")
        return AgentResult(status="ok", message_type=self._message_type, data=dict(self._data))


class WaveBarrierAgent(BaseAgent):
    """并行波次探测：两个实例互相等待对方 start，串行执行会超时失败。"""

    def __init__(self, name: str, started: set, release: asyncio.Event, log: list) -> None:
        self.name = name
        self._started = started
        self._release = release
        self._log = log

    async def run(self, state, emit) -> AgentResult:
        self._log.append(("start", self.name))
        self._started.add(self.name)
        if len(self._started) >= 2:
            self._release.set()
        await asyncio.wait_for(self._release.wait(), timeout=5)
        self._log.append(("end", self.name))
        return AgentResult(status="ok", message_type="data_result", data={"agent": self.name})


def test_parallel_wave_overlap():
    """两个无依赖步骤并发执行：双方都在对方 end 前已 start（并发窗口重叠）。"""

    async def main():
        started: set = set()
        release = asyncio.Event()
        log: list = []
        a = WaveBarrierAgent("agent_a", started, release, log)
        b = WaveBarrierAgent("agent_b", started, release, log)
        state = make_running_state()
        state.plan_steps = [
            PlanStep(id="s1", agent="agent_a"),
            PlanStep(id="s2", agent="agent_b"),
        ]
        events, emit = event_recorder()
        await asyncio.wait_for(WorkflowExecutor(make_registry(a, b)).execute(state, emit), timeout=10)
        return state, events, log

    state, events, log = asyncio.run(main())
    assert state.status is TaskStatus.COMPLETED
    # 两个 start 都发生在任何 end 之前 → 两步执行窗口重叠（真正并行）
    assert {name for kind, name in log[:2]} == {"agent_a", "agent_b"}
    assert all(kind == "start" for kind, _ in log[:2])
    # 事件序：两笔 agent_start 先于任何 agent_end
    agent_events = [e for e in events if e["type"] in ("agent_start", "agent_end")]
    assert [e["type"] for e in agent_events[:2]] == ["agent_start", "agent_start"]
    assert agent_events[0]["step"] != agent_events[1]["step"]


def test_retry_flaky_second_attempt_succeeds(monkeypatch):
    """flaky 第 2 次成功：retry 事件 1 次（retry_count=1），agent 共调用 2 次。"""
    monkeypatch.setattr(executor_mod, "_RETRY_BASE_S", 0.0)  # 测试加速：退避间隔置 0

    async def main():
        flaky = ScriptedAgent("flaky", error_first=1)
        state = make_running_state()
        state.plan_steps = [PlanStep(id="s1", agent="flaky")]
        events, emit = event_recorder()
        result_state = await WorkflowExecutor(make_registry(flaky)).execute(state, emit)
        return result_state, events, flaky

    state, events, flaky = asyncio.run(main())
    assert flaky.calls == 2  # 第 1 次失败 + 第 2 次成功
    retries = [e for e in events if e["type"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["step"] == "s1"
    assert retries[0]["agent"] == "flaky"
    assert retries[0]["retry_count"] == 1
    # 重试耗尽前有一次失败的 agent_end（status=error），成功后有一次 ok
    ends = [e for e in events if e["type"] == "agent_end"]
    assert [e["status"] for e in ends] == ["error", "ok"]
    assert state.status is TaskStatus.COMPLETED
    assert any(e["type"] == "final" for e in events)


def test_optional_step_exhausted_is_skipped(monkeypatch):
    """optional 步骤重试耗尽 → step_skipped 事件，任务继续并 completed。"""
    monkeypatch.setattr(executor_mod, "_RETRY_BASE_S", 0.0)

    async def main():
        bad = ScriptedAgent("bad", error_first=99)  # 永远失败
        ok = ScriptedAgent("ok")
        state = make_running_state()
        state.plan_steps = [
            PlanStep(id="s_bad", agent="bad", params={"optional": True}),
            PlanStep(id="s_ok", agent="ok"),
        ]
        events, emit = event_recorder()
        result_state = await WorkflowExecutor(make_registry(bad, ok)).execute(state, emit)
        return result_state, events, bad, ok

    state, events, bad, ok = asyncio.run(main())
    # 重试耗尽（MAX_RETRY=2 → 共 3 次调用）后跳过而非终态失败
    assert bad.calls == 3
    skipped = [e for e in events if e["type"] == "step_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["step"] == "s_bad"
    assert "reason" in skipped[0]
    assert state.status is TaskStatus.COMPLETED
    assert any(e["type"] == "final" for e in events)
    assert state.results["ok"] == {"agent": "ok"}


def test_required_step_exhausted_fails_final(monkeypatch):
    """必经步骤重试耗尽 → failed_final，且不发出 final 事件。"""
    monkeypatch.setattr(executor_mod, "_RETRY_BASE_S", 0.0)

    async def main():
        bad = ScriptedAgent("bad", error_first=99)
        state = make_running_state()
        state.plan_steps = [PlanStep(id="s_bad", agent="bad")]
        events, emit = event_recorder()
        result_state = await WorkflowExecutor(make_registry(bad)).execute(state, emit)
        return result_state, events, bad

    state, events, bad = asyncio.run(main())
    assert bad.calls == 3  # 1 + MAX_RETRY(2)
    assert state.status is TaskStatus.FAILED_FINAL
    assert not any(e["type"] == "final" for e in events)
    # 每次失败都有 error 事件，且至少一次 agent_end(status=error)
    assert sum(1 for e in events if e["type"] == "error") == 3
    assert any(e["type"] == "agent_end" and e["status"] == "error" for e in events)


def test_success_appends_agent_messages():
    """成功后 state.messages 含 AgentMessage：sender/receiver/type/payload 正确。"""

    async def main():
        alpha = ScriptedAgent("alpha", data={"value": 1})
        beta = ScriptedAgent("beta", data={"value": 2})
        state = make_running_state()
        state.plan_steps = [
            PlanStep(id="s1", agent="alpha"),
            PlanStep(id="s2", agent="beta", depends_on=["s1"]),
        ]
        events, emit = event_recorder()
        result_state = await WorkflowExecutor(make_registry(alpha, beta)).execute(state, emit)
        return result_state, events

    state, events = asyncio.run(main())
    assert state.status is TaskStatus.COMPLETED
    assert len(state.messages) == 2

    m1, m2 = state.messages
    assert m1.sender == "alpha"
    assert m1.receiver == ["beta"]  # 下游 agent 名列表
    assert m1.type == "data_result"
    assert m1.payload == {"value": 1}
    assert m1.task_id == state.task_id

    assert m2.sender == "beta"
    assert m2.receiver == []  # 无下游
    assert m2.payload == {"value": 2}

    # agent_end 的 detail 带上 message_id / message_type
    ends = {e["step"]: e for e in events if e["type"] == "agent_end"}
    assert ends["s1"]["detail"]["message_id"] == m1.message_id
    assert ends["s1"]["detail"]["message_type"] == "data_result"


def test_execute_rejects_plan_over_max_steps():
    """execute 对超步数计划直接抛 PlanError（validate_dag 首次强制）。"""
    state = make_running_state()
    state.plan_steps = [
        PlanStep(id=f"s{i}", agent="x", depends_on=([f"s{i-1}"] if i else []))
        for i in range(99)
    ]
    events, emit = event_recorder()

    async def main():
        await WorkflowExecutor(AgentRegistry()).execute(state, emit)

    try:
        asyncio.run(main())
    except PlanError:
        pass
    else:  # pragma: no cover
        raise AssertionError("预期抛出 PlanError")
