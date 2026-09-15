"""Agent 工作流集成测试：mock LLM + 真实 ReportSynthesizer 全链路。

覆盖：数据分析全链路、匹配全链路、简单问题跳过 validator、
复杂问题全步骤、报告合成器输出格式。
与 test_workflow.py 互补：本文件引入真实 ReportSynthesizer 并断言报告结构。
"""
from __future__ import annotations

import asyncio

from app.agent_runtime.executor import WorkflowExecutor
from app.agent_runtime.planner import build_data_plan, build_match_plan
from app.agent_runtime.registry import AgentRegistry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import AgentResult, BaseAgent
from app.agents.report_agent import ReportSynthesizer


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
        return AgentResult(
            status="ok", message_type=self._message_type, data=dict(self._data)
        )


def make_running_state(query: str = "分析数据", dataset_id: str = "ds-test") -> TaskState:
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
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    return events, emit


# ---------- 数据分析工作流（含真实 ReportSynthesizer） ----------


class TestDataAnalysisWorkflow:
    def test_full_data_analysis_with_real_report(self, monkeypatch):
        """完整数据链路 + 真实 ReportSynthesizer：报告 title/summary/engine 正确。"""
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        query = "对比各地区销售额分布趋势"
        steps = build_data_plan(query)
        assert [s.id for s in steps] == ["data", "validator", "report"]

        data_agent = MockAgent(
            "data_agent",
            data={
                "final": {
                    "task_id": "",
                    "query": query,
                    "engine": "duckdb",
                    "sql": "SELECT region, SUM(sales) FROM t GROUP BY region",
                    "columns": ["region", "total_sales"],
                    "rows": [["华东", 1200], ["华北", 800]],
                    "row_count": 2,
                    "truncated": False,
                    "chart": None,
                    "explanation": "按地区汇总销售额，华东领先",
                },
            },
        )
        validator = MockAgent(
            "validator_agent",
            data={"valid": True},
            message_type="validation_result",
        )
        report = ReportSynthesizer()

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
        assert result_state.final_result["columns"] == ["region", "total_sales"]

        # 验证真实 ReportSynthesizer 写入的结果
        report_data = result_state.results["report_synthesizer"]
        assert report_data["source"] == "template"
        assert report_data["report"]["title"] == "数据分析报告"
        assert report_data["report"]["summary"] == "按地区汇总销售额，华东领先"
        assert report_data["report"]["engine"] == "duckdb"
        assert report_data["report"]["row_count"] == 2
        assert report_data["report"]["columns"] == ["region", "total_sales"]

    def test_data_analysis_message_chain(self):
        """AgentMessage 链：data → validator → report，receiver 正确。"""
        query = "对比各地区趋势"
        steps = build_data_plan(query)

        data_agent = MockAgent("data_agent", data={"final": {"columns": ["a"]}})
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

        assert len(result_state.messages) == 3
        by_sender = {m.sender: m for m in result_state.messages}
        assert by_sender["data_agent"].receiver == ["validator_agent"]
        assert by_sender["validator_agent"].receiver == ["report_synthesizer"]
        assert by_sender["report_synthesizer"].receiver == []


# ---------- 匹配工作流（含真实 ReportSynthesizer） ----------


class TestMatchWorkflow:
    def test_full_match_with_real_report(self, monkeypatch):
        """匹配链路 + 真实 ReportSynthesizer：报告含 score/level/suggestions。"""
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        steps = build_match_plan()

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
                "resume": {"resume_id": "r-1"},
                "jobs": [{"id": 1, "title": "后端工程师"}],
                "score": 85,
                "dimensions": {"skill": 90, "project": 80},
                "skill_gap": ["Kubernetes", "Docker"],
                "interpretation": "高度匹配",
                "interpretation_source": "mock",
            },
            message_type="match_result",
        )
        validator = MockAgent("validator_agent", message_type="validation_result")
        report = ReportSynthesizer()

        state = make_running_state(query="匹配岗位", dataset_id=None)
        state.context["resume"] = {"resume_id": "r-1"}
        state.plan_steps = steps
        events, emit = event_recorder()

        result_state = asyncio.run(
            WorkflowExecutor(
                make_registry(resume_agent, job_agent, match_agent, validator, report)
            ).execute(state, emit)
        )

        assert result_state.status is TaskStatus.COMPLETED
        assert result_state.final_result["score"] == 85

        report_data = result_state.results["report_synthesizer"]
        assert report_data["report"]["title"] == "简历匹配分析报告"
        assert report_data["report"]["score"] == 85
        assert report_data["report"]["level"] == "优秀"
        assert report_data["report"]["skill_gap"] == ["Kubernetes", "Docker"]
        # skill=90, project=80 都 ≥80 → 两条亮点
        assert len(report_data["report"]["highlights"]) == 2
        # score ≥ 60 → 无升级建议；有 gap → 有补齐建议
        suggestions = report_data["report"]["suggestions"]
        assert len(suggestions) == 1
        assert "优先补齐：Kubernetes, Docker" in suggestions[0]

    def test_match_final_result_structure(self):
        """匹配链路 final_result 包含全部 §2.1 必需字段。"""
        steps = build_match_plan()
        agents = [
            MockAgent("resume_agent", data={"resume_id": "r-x"}, message_type="resume_profile"),
            MockAgent("job_agent", data={"jobs": []}, message_type="job_profile"),
            MockAgent(
                "match_agent",
                data={
                    "resume": {"resume_id": "r-x"},
                    "jobs": [],
                    "score": 0.5,
                    "dimensions": {"skill": 50},
                    "skill_gap": ["Go"],
                    "interpretation": "一般",
                    "interpretation_source": "mock",
                },
                message_type="match_result",
            ),
            MockAgent("validator_agent", message_type="validation_result"),
            MockAgent("report_synthesizer", message_type="report"),
        ]

        state = make_running_state(query="匹配", dataset_id=None)
        state.plan_steps = steps
        events, emit = event_recorder()

        result_state = asyncio.run(
            WorkflowExecutor(make_registry(*agents)).execute(state, emit)
        )

        final = result_state.final_result
        assert final["engine"] == "multi_agent"
        assert "score" in final
        assert "dimensions" in final
        assert "skill_gap" in final
        assert "interpretation" in final
        assert "resume" in final
        assert "jobs" in final


# ---------- 动态路由：简单 vs 复杂 ----------


class TestDynamicRouting:
    def test_simple_query_skips_validator(self):
        """简单问题计划只有 data，validator 不被调用。"""
        query = "总销售额是多少"
        steps = build_data_plan(query)
        assert [s.id for s in steps] == ["data"]

        data_agent = MockAgent(
            "data_agent",
            data={"final": {"columns": ["total"], "rows": [[4200]], "row_count": 1}},
        )
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
        assert len(result_state.messages) == 1  # 只有 data_agent 的消息

    def test_complex_query_includes_all_steps(self):
        """复杂问题计划含 data+validator+report，全部执行。"""
        query = "对比各地区销售额分布趋势"
        steps = build_data_plan(query)
        assert [s.id for s in steps] == ["data", "validator", "report"]

        data_agent = MockAgent("data_agent", data={"final": {"columns": ["a"]}})
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
        assert all(a.calls == 1 for a in (data_agent, validator, report))
        assert len(result_state.messages) == 3


# ---------- 报告合成器输出格式 ----------


class TestReportSynthesizerOutputFormat:
    def test_match_report_level_thresholds(self, monkeypatch):
        """匹配报告等级：≥80 优秀，≥60 良好，≥40 一般，<40 较低。"""
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()

        for score, expected_level in [
            (95, "优秀"),
            (80, "优秀"),
            (70, "良好"),
            (60, "良好"),
            (50, "一般"),
            (40, "一般"),
            (20, "较低"),
        ]:
            state = TaskState()
            state.results["match_agent"] = {
                "score": score,
                "dimensions": {},
                "skill_gap": [],
            }

            async def main():
                async def emit(e):
                    pass

                return await agent.run(state, emit)

            result = asyncio.run(main())
            assert result.data["report"]["level"] == expected_level, (
                f"score={score} 应为 {expected_level}，实际 {result.data['report']['level']}"
            )

    def test_data_report_contains_all_fields(self, monkeypatch):
        """数据报告包含 title/summary/query/engine/row_count/columns。"""
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()
        state = TaskState()
        state.results["data_agent"] = {
            "final": {
                "explanation": "分析结果",
                "query": "统计销售额",
                "engine": "duckdb",
                "row_count": 42,
                "columns": ["region", "sales"],
                "elapsed_ms": 150,
            }
        }

        async def main():
            async def emit(e):
                pass

            return await agent.run(state, emit)

        result = asyncio.run(main())
        report = result.data["report"]
        assert report["title"] == "数据分析报告"
        assert report["summary"] == "分析结果"
        assert report["query"] == "统计销售额"
        assert report["engine"] == "duckdb"
        assert report["row_count"] == 42
        assert report["columns"] == ["region", "sales"]
        assert report["elapsed_ms"] == 150

    def test_empty_results_fallback(self, monkeypatch):
        """无上游结果时报告 fallback 为「任务完成」。"""
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()
        state = TaskState()

        async def main():
            async def emit(e):
                pass

            return await agent.run(state, emit)

        result = asyncio.run(main())
        assert result.status == "ok"
        assert result.data["report"]["summary"] == "任务完成"
        assert result.data["source"] == "template"

    def test_highlights_extraction(self, monkeypatch):
        """高分维度（≥80）出现在 highlights，低分不出。"""
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()
        state = TaskState()
        state.results["match_agent"] = {
            "score": 75,
            "dimensions": {"skill": 92, "project": 78, "education": 85},
            "skill_gap": [],
        }

        async def main():
            async def emit(e):
                pass

            return await agent.run(state, emit)

        result = asyncio.run(main())
        highlights = result.data["report"]["highlights"]
        assert "技能匹配：92 分" in highlights
        assert "教育背景：85 分" in highlights
        assert not any("78" in h for h in highlights)
