"""report_agent 单测：_match_report / _data_report / _extract_highlights / _generate_suggestions。"""
from __future__ import annotations

import asyncio

from app.agent_runtime.state import TaskState
from app.agents.report_agent import ReportSynthesizer


# ---------- _match_report ----------


class TestMatchReport:
    def test_score_90_is_excellent(self):
        agent = ReportSynthesizer()
        results = {
            "match_agent": {
                "score": 90,
                "dimensions": {"skill": 95, "project": 80},
                "skill_gap": ["kubernetes"],
            }
        }
        report = agent._match_report(results)
        assert report["title"] == "简历匹配分析报告"
        assert report["score"] == 90
        assert report["level"] == "优秀"
        assert report["dimensions"] == {"skill": 95, "project": 80}
        assert report["skill_gap"] == ["kubernetes"]
        assert "综合匹配度 90 分（优秀）" in report["summary"]

    def test_score_70_is_good(self):
        agent = ReportSynthesizer()
        report = agent._match_report({"match_agent": {"score": 70, "dimensions": {}, "skill_gap": []}})
        assert report["level"] == "良好"

    def test_score_50_is_average(self):
        agent = ReportSynthesizer()
        report = agent._match_report({"match_agent": {"score": 50, "dimensions": {}, "skill_gap": []}})
        assert report["level"] == "一般"

    def test_score_20_is_low(self):
        agent = ReportSynthesizer()
        report = agent._match_report({"match_agent": {"score": 20, "dimensions": {}, "skill_gap": []}})
        assert report["level"] == "较低"

    def test_skill_gap_truncated_to_10(self):
        agent = ReportSynthesizer()
        gap = [f"skill_{i}" for i in range(15)]
        report = agent._match_report({"match_agent": {"score": 50, "dimensions": {}, "skill_gap": gap}})
        assert len(report["skill_gap"]) == 10
        assert report["skill_gap"][0] == "skill_0"

    def test_missing_keys_use_defaults(self):
        agent = ReportSynthesizer()
        report = agent._match_report({"match_agent": {}})
        assert report["score"] == 0
        assert report["level"] == "较低"
        assert report["dimensions"] == {}
        assert report["skill_gap"] == []


# ---------- _data_report ----------


class TestDataReport:
    def test_full_final_result(self):
        agent = ReportSynthesizer()
        results = {
            "data_agent": {
                "final": {
                    "explanation": "销售额总计 100 万",
                    "query": "统计总销售额",
                    "engine": "duckdb",
                    "row_count": 42,
                    "columns": ["region", "sales"],
                    "elapsed_ms": 120,
                }
            }
        }
        report = agent._data_report(results)
        assert report["title"] == "数据分析报告"
        assert report["summary"] == "销售额总计 100 万"
        assert report["query"] == "统计总销售额"
        assert report["engine"] == "duckdb"
        assert report["row_count"] == 42
        assert report["columns"] == ["region", "sales"]
        assert report["elapsed_ms"] == 120

    def test_empty_data_agent_uses_defaults(self):
        agent = ReportSynthesizer()
        report = agent._data_report({"data_agent": {}})
        assert report["summary"] == "分析完成"
        assert report["query"] == ""
        assert report["engine"] == ""
        assert report["row_count"] == 0
        assert report["columns"] == []
        assert report["elapsed_ms"] == 0


# ---------- _extract_highlights ----------


class TestExtractHighlights:
    def test_high_scores_produce_highlights(self):
        agent = ReportSynthesizer()
        match = {"dimensions": {"skill": 95, "project": 85, "experience": 70}}
        highlights = agent._extract_highlights(match)
        assert highlights == ["技能匹配：95 分", "项目经验：85 分"]

    def test_low_scores_ignored(self):
        agent = ReportSynthesizer()
        match = {"dimensions": {"skill": 50, "project": 30}}
        assert agent._extract_highlights(match) == []

    def test_boundary_score_80_included(self):
        agent = ReportSynthesizer()
        match = {"dimensions": {"education": 80}}
        assert agent._extract_highlights(match) == ["教育背景：80 分"]

    def test_score_79_excluded(self):
        agent = ReportSynthesizer()
        match = {"dimensions": {"education": 79}}
        assert agent._extract_highlights(match) == []

    def test_unknown_dim_falls_back_to_raw_key(self):
        agent = ReportSynthesizer()
        match = {"dimensions": {"custom_dim": 90}}
        assert agent._extract_highlights(match) == ["custom_dim：90 分"]

    def test_empty_dimensions(self):
        agent = ReportSynthesizer()
        assert agent._extract_highlights({"dimensions": {}}) == []


# ---------- _generate_suggestions ----------


class TestGenerateSuggestions:
    def test_low_score_gets_upgrade_suggestion(self):
        agent = ReportSynthesizer()
        suggestions = agent._generate_suggestions(40, [])
        assert any("提升核心技能" in s for s in suggestions)

    def test_high_score_no_upgrade_suggestion(self):
        agent = ReportSynthesizer()
        suggestions = agent._generate_suggestions(80, [])
        assert not any("提升核心技能" in s for s in suggestions)

    def test_gap_produces_fill_suggestion(self):
        agent = ReportSynthesizer()
        suggestions = agent._generate_suggestions(80, ["kubernetes", "docker", "git"])
        assert len(suggestions) == 1
        assert "优先补齐：kubernetes, docker, git" in suggestions[0]

    def test_gap_truncated_to_top_3(self):
        agent = ReportSynthesizer()
        gap = ["a", "b", "c", "d", "e"]
        suggestions = agent._generate_suggestions(50, gap)
        fill = [s for s in suggestions if s.startswith("优先补齐")]
        assert len(fill) == 1
        assert "a, b, c" in fill[0]
        assert "d" not in fill[0]

    def test_low_score_with_gap_both_suggestions(self):
        agent = ReportSynthesizer()
        suggestions = agent._generate_suggestions(30, ["rust"])
        assert len(suggestions) == 2

    def test_high_score_no_gap_empty(self):
        agent = ReportSynthesizer()
        assert agent._generate_suggestions(90, []) == []


# ---------- run() 集成 ----------


class TestRun:
    def test_run_match_chain_writes_results(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()
        state = TaskState()
        state.results["match_agent"] = {
            "score": 88,
            "dimensions": {"skill": 90},
            "skill_gap": ["go"],
        }

        async def main():
            async def emit(event: dict) -> None:
                pass

            return await agent.run(state, emit)

        result = asyncio.run(main())
        assert result.status == "ok"
        assert result.message_type == "report"
        assert result.data["source"] == "template"
        assert "report_synthesizer" in state.results
        assert result.data["report"]["level"] == "优秀"

    def test_run_data_chain_writes_results(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()
        state = TaskState()
        state.results["data_agent"] = {
            "final": {"explanation": "ok", "columns": ["a"], "row_count": 1}
        }

        async def main():
            async def emit(event: dict) -> None:
                pass

            return await agent.run(state, emit)

        result = asyncio.run(main())
        assert result.status == "ok"
        assert result.data["report"]["title"] == "数据分析报告"

    def test_run_empty_results_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.app_settings.get_setting",
            lambda key, default=None: "false",
        )
        agent = ReportSynthesizer()
        state = TaskState()

        async def main():
            async def emit(event: dict) -> None:
                pass

            return await agent.run(state, emit)

        result = asyncio.run(main())
        assert result.status == "ok"
        assert result.data["report"]["summary"] == "任务完成"
