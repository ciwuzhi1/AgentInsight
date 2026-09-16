"""工作流集成测试：TaskState → planner → Supervisor/Executor 全链路（mock agents）。

覆盖数据分析链路、匹配链路、简单问题动态跳过 validator、复杂问题全步骤。
参照 tests/unit/test_executor.py 的 DummyAgent / event_recorder 模式，全程离线。
"""
from __future__ import annotations

import asyncio

from app.agent_runtime.executor import WorkflowExecutor
from app.agent_runtime.planner import build_data_plan, build_match_plan
from app.agent_runtime.registry import AgentRegistry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agent_runtime.supervisor import Supervisor
from app.agents.base import AgentResult, BaseAgent


class MockAgent(BaseAgent):
    """可复用 mock agent：记录调用次数，返回固定 data。"""

    def __init__(
        self,
        name: str,
        data: dict | None = None,
        message_type: str = "data_result",
    ) -> None:
        self.name = name
        self.calls = 0
        self._data = data if data is not None else {"agent": name}
        self._message_type = message_type

    async def run(self, state, emit) -> AgentResult:
        self.calls += 1
        await emit(
            {
                "type": "mock_progress",
                "agent": self.name,
                "call": self.calls,
                "task_id": state.task_id,
            }
        )
        return AgentResult(
            status="ok", message_type=self._message_type, data=dict(self._data)
        )


def make_running_state(query: str = "分析数据", dataset_id: str = "ds-test") -> TaskState:
    """构造已处于 RUNNING 的任务状态（supervisor 进入 executor 前的状态）。"""
    state = TaskState(dataset_id=dataset_id, query=query)
    state.transition(TaskStatus.ROUTING)
    state.transition(TaskStatus.RUNNING)
    return state


def make_registry(*agents: BaseAgent) -> AgentRegistry:
    reg = AgentRegistry()
    for a in agents:
        reg.register(a)
    return reg


def event_recorder():
    """返回 (events, emit)：记录 executor/supervisor 发出的全部事件。"""
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    return events, emit


def agent_start_names(events: list[dict]) -> list[str]:
    return [e["agent"] for e in events if e["type"] == "agent_start"]


# ---------- 数据分析工作流 ----------


def test_data_analysis_workflow_end_to_end():
    """完整数据链路：complex 查询 → data/validator/report 全部执行 → completed。"""
    query = "对比各地区销售额分布趋势"  # 含复杂特征 → 三步计划
    steps = build_data_plan(query)
    assert [s.id for s in steps] == ["data", "validator", "report"]

    data_agent = MockAgent(
        "data_agent",
        data={
            "engine": "duckdb",
            "sql": "SELECT region, SUM(sales) FROM t GROUP BY region LIMIT 1000",
            "explanation": "按地区汇总销售额",
            "final": {
                "task_id": "",
                "query": query,
                "engine": "duckdb",
                "sql": "SELECT region, SUM(sales) FROM t GROUP BY region LIMIT 1000",
                "columns": ["region", "total_sales"],
                "rows": [["华东", 1200], ["华北", 800]],
                "row_count": 2,
                "truncated": False,
                "chart": None,
            },
        },
    )
    validator = MockAgent(
        "validator_agent",
        data={"valid": True, "checks": ["row_count", "schema"]},
        message_type="validation_result",
    )
    report = MockAgent(
        "report_synthesizer",
        data={"summary": "华东领先，华北次之"},
        message_type="report",
    )

    state = make_running_state(query)
    state.plan_steps = steps
    events, emit = event_recorder()

    result_state = asyncio.run(
        WorkflowExecutor(make_registry(data_agent, validator, report)).execute(
            state, emit
        )
    )

    assert result_state.status is TaskStatus.COMPLETED
    assert data_agent.calls == 1
    assert validator.calls == 1
    assert report.calls == 1
    # final_result 组装自 data_agent.final（匹配链路除外）
    assert result_state.final_result["columns"] == ["region", "total_sales"]
    assert result_state.final_result["row_count"] == 2
    # 每个 agent 都写入了 AgentMessage
    assert len(result_state.messages) == 3
    assert {m.sender for m in result_state.messages} == {
        "data_agent",
        "validator_agent",
        "report_synthesizer",
    }
    # 事件序：agent_start/end 齐全 + final
    assert any(e["type"] == "final" for e in events)
    assert set(agent_start_names(events)) == {
        "data_agent",
        "validator_agent",
        "report_synthesizer",
    }


# ---------- 匹配工作流 ----------


def test_match_workflow_end_to_end():
    """匹配链路：resume/job 并行 → match → validator → report，final 含 score。"""
    steps = build_match_plan()
    by_id = {s.id: s for s in steps}
    assert set(by_id) == {"resume", "job", "match", "validator", "report"}

    resume_agent = MockAgent(
        "resume_agent",
        data={"resume_id": "r-1", "skills": ["Python", "SQL"]},
        message_type="resume_profile",
    )
    job_agent = MockAgent(
        "job_agent",
        data={"jobs": [{"id": 1, "title": "后端工程师"}]},
        message_type="job_profile",
    )
    match_agent = MockAgent(
        "match_agent",
        data={
            "resume": {"resume_id": "r-1", "skills": ["Python", "SQL"]},
            "jobs": [{"id": 1, "title": "后端工程师"}],
            "score": 0.86,
            "dimensions": {"skill": 0.9, "experience": 0.8},
            "skill_gap": ["Kubernetes"],
            "interpretation": "技能高度匹配，缺少 K8s 经验",
            "interpretation_source": "mock",
        },
        message_type="match_result",
    )
    validator = MockAgent(
        "validator_agent",
        data={"valid": True},
        message_type="validation_result",
    )
    report = MockAgent(
        "report_synthesizer",
        data={"summary": "推荐投递"},
        message_type="report",
    )

    state = make_running_state(query="帮我匹配合适岗位", dataset_id=None)
    state.context["resume"] = {"resume_id": "r-1"}
    state.plan_steps = steps
    events, emit = event_recorder()

    result_state = asyncio.run(
        WorkflowExecutor(
            make_registry(resume_agent, job_agent, match_agent, validator, report)
        ).execute(state, emit)
    )

    assert result_state.status is TaskStatus.COMPLETED
    assert resume_agent.calls == 1
    assert job_agent.calls == 1
    assert match_agent.calls == 1
    assert validator.calls == 1
    assert report.calls == 1

    final = result_state.final_result
    assert final["engine"] == "multi_agent"
    assert final["score"] == 0.86
    assert final["skill_gap"] == ["Kubernetes"]
    assert final["resume"]["resume_id"] == "r-1"
    assert final["jobs"] == [{"id": 1, "title": "后端工程师"}]
    assert any(e["type"] == "final" for e in events)


# ---------- 动态路由：简单 vs 复杂 ----------


def test_simple_query_skips_validator():
    """简单问题（短句+聚合词）计划只有 data，执行时无 validator。"""
    query = "总销售额是多少"
    steps = build_data_plan(query)
    assert [s.id for s in steps] == ["data"]

    data_agent = MockAgent(
        "data_agent",
        data={
            "engine": "duckdb",
            "final": {
                "columns": ["total_sales"],
                "rows": [[4200]],
                "row_count": 1,
                "truncated": False,
                "chart": None,
            },
        },
    )
    # 故意注册 validator：若计划包含它则会被调用；简单链路不应走到
    validator = MockAgent("validator_agent", message_type="validation_result")

    state = make_running_state(query)
    state.plan_steps = steps
    events, emit = event_recorder()

    result_state = asyncio.run(
        WorkflowExecutor(make_registry(data_agent, validator)).execute(state, emit)
    )

    assert result_state.status is TaskStatus.COMPLETED
    assert data_agent.calls == 1
    assert validator.calls == 0
    names = agent_start_names(events)
    assert "data_agent" in names
    assert "validator_agent" not in names
    assert result_state.final_result["row_count"] == 1


def test_complex_query_includes_all_steps():
    """复杂问题（对比/分布/趋势）计划含 data+validator+report，全部执行。"""
    query = "对比各地区销售额分布趋势"
    steps = build_data_plan(query)
    assert [s.id for s in steps] == ["data", "validator", "report"]

    data_agent = MockAgent(
        "data_agent",
        data={"final": {"columns": ["region"], "rows": [["华东"]], "row_count": 1}},
    )
    validator = MockAgent("validator_agent", message_type="validation_result")
    report = MockAgent("report_synthesizer", message_type="report")

    state = make_running_state(query)
    state.plan_steps = steps
    events, emit = event_recorder()

    result_state = asyncio.run(
        WorkflowExecutor(make_registry(data_agent, validator, report)).execute(
            state, emit
        )
    )

    assert result_state.status is TaskStatus.COMPLETED
    assert data_agent.calls == 1
    assert validator.calls == 1
    assert report.calls == 1
    assert set(agent_start_names(events)) == {
        "data_agent",
        "validator_agent",
        "report_synthesizer",
    }


# ---------- Supervisor 端到端（路由 + 执行） ----------


def test_supervisor_routes_and_runs_data_workflow():
    """Supervisor.run_task：有 dataset 无 resume → data 链路，事件含 plan/final。"""
    query = "对比各地区销售额趋势"
    data_agent = MockAgent(
        "data_agent",
        data={
            "engine": "duckdb",
            "final": {
                "columns": ["region", "total"],
                "rows": [["华东", 100]],
                "row_count": 1,
                "truncated": False,
                "chart": None,
            },
        },
    )
    validator = MockAgent("validator_agent", message_type="validation_result")
    report = MockAgent("report_synthesizer", message_type="report")

    state = TaskState(dataset_id="ds-1", query=query)
    events, emit = event_recorder()

    result_state = asyncio.run(
        Supervisor(make_registry(data_agent, validator, report)).run_task(state, emit)
    )

    assert result_state.status is TaskStatus.COMPLETED
    assert [s.id for s in result_state.plan_steps] == ["data", "validator", "report"]
    assert result_state.plan == ["data_agent", "validator_agent", "report_synthesizer"]

    types = [e["type"] for e in events]
    assert "state" in types  # routing
    assert "plan" in types
    assert "final" in types

    plan_events = [e for e in events if e["type"] == "plan"]
    assert plan_events and plan_events[0]["steps"][0]["agent"] == "data_agent"
    assert result_state.final_result["columns"] == ["region", "total"]


def test_supervisor_routes_and_runs_match_workflow():
    """Supervisor.run_task：context 有 resume + 求职关键词 → 匹配链路。"""
    agents = {
        "resume_agent": MockAgent(
            "resume_agent", data={"resume_id": "r-9"}, message_type="resume_profile"
        ),
        "job_agent": MockAgent(
            "job_agent", data={"jobs": [{"id": 7}]}, message_type="job_profile"
        ),
        "match_agent": MockAgent(
            "match_agent",
            data={
                "resume": {"resume_id": "r-9"},
                "jobs": [{"id": 7}],
                "score": 0.7,
                "dimensions": {},
                "skill_gap": [],
                "interpretation": "一般匹配",
                "interpretation_source": "mock",
            },
            message_type="match_result",
        ),
        "validator_agent": MockAgent(
            "validator_agent", message_type="validation_result"
        ),
        "report_synthesizer": MockAgent("report_synthesizer", message_type="report"),
    }

    state = TaskState(dataset_id=None, query="看看这个简历和岗位匹配吗")
    state.context["resume"] = {"resume_id": "r-9"}
    events, emit = event_recorder()

    result_state = asyncio.run(
        Supervisor(make_registry(*agents.values())).run_task(state, emit)
    )

    assert result_state.status is TaskStatus.COMPLETED
    assert [s.id for s in result_state.plan_steps] == [
        "resume",
        "job",
        "match",
        "validator",
        "report",
    ]
    assert result_state.final_result["engine"] == "multi_agent"
    assert result_state.final_result["score"] == 0.7
    assert all(a.calls == 1 for a in agents.values())
