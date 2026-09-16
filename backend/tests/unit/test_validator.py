"""ValidatorAgent 单测：report 校验 / score-dimensions 一致性 / 非法格式。

全程离线，不依赖 MySQL / Docker / 网络。
"""
from __future__ import annotations

import asyncio

from app.agent_runtime.state import TaskState
from app.agents.validator_agent import (
    ValidatorAgent,
    _validate_match,
    _validate_report,
    _validate_score_dimensions_consistency,
)


# ---------- _validate_report ----------


class TestValidateReport:
    def test_valid_template_report(self):
        """合法模板报告：source=template，title/summary 齐全 → 无错误。"""
        result = {
            "report": {"title": "简历匹配分析报告", "summary": "综合匹配度 85 分（优秀）"},
            "source": "template",
            "elapsed_ms": 12,
        }
        assert _validate_report(result) == []

    def test_valid_llm_report(self):
        """合法 LLM 报告：source=llm → 无错误。"""
        result = {
            "report": {"title": "数据分析报告", "summary": "分析完成"},
            "source": "llm",
            "elapsed_ms": 30,
        }
        assert _validate_report(result) == []

    def test_missing_result_returns_error(self):
        """None / 空 dict → 报缺少结果错误。"""
        assert len(_validate_report(None)) == 1
        assert len(_validate_report({})) == 1

    def test_invalid_source(self):
        """source 非法值 → 报错。"""
        result = {
            "report": {"title": "T", "summary": "S"},
            "source": "unknown",
            "elapsed_ms": 0,
        }
        errors = _validate_report(result)
        assert any("source" in e for e in errors)

    def test_missing_source(self):
        """source 缺失 → 报错。"""
        result = {"report": {"title": "T", "summary": "S"}, "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("source" in e for e in errors)

    def test_report_not_dict(self):
        """report 非 dict → 报错。"""
        result = {"report": "not a dict", "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("report 必须为 dict" in e for e in errors)

    def test_missing_title(self):
        """title 缺失 → 报错。"""
        result = {"report": {"summary": "S"}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("title" in e for e in errors)

    def test_empty_title(self):
        """title 为空字符串 → 报错。"""
        result = {"report": {"title": "", "summary": "S"}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("title" in e for e in errors)

    def test_whitespace_only_title(self):
        """title 仅空白 → 报错。"""
        result = {"report": {"title": "   ", "summary": "S"}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("title" in e for e in errors)

    def test_missing_summary(self):
        """summary 缺失 → 报错。"""
        result = {"report": {"title": "T"}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("summary" in e for e in errors)

    def test_empty_summary(self):
        """summary 为空字符串 → 报错。"""
        result = {"report": {"title": "T", "summary": ""}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("summary" in e for e in errors)

    def test_title_not_str(self):
        """title 非 str → 报错。"""
        result = {"report": {"title": 123, "summary": "S"}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("title" in e for e in errors)

    def test_summary_not_str(self):
        """summary 非 str → 报错。"""
        result = {"report": {"title": "T", "summary": None}, "source": "template", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert any("summary" in e for e in errors)

    def test_multiple_errors_collected(self):
        """多个字段非法时应全部收集。"""
        result = {"report": {"title": "", "summary": ""}, "source": "bad", "elapsed_ms": 0}
        errors = _validate_report(result)
        assert len(errors) >= 3


# ---------- score-dimensions 一致性 ----------


def _make_match(score: int, **dim_overrides) -> dict:
    """构造合法 match dict，可覆盖任意维度。"""
    dims = {"skill": 80, "project": 80, "experience": 80, "education": 80, "engineering": 80}
    dims.update(dim_overrides)
    return {
        "score": score,
        "dimensions": dims,
        "skill_gap": ["Docker"],
        "interpretation": "综合匹配",
    }


class TestScoreDimensionsConsistency:
    def test_consistent_score_passes(self):
        """score 与加权结果一致 → 无错误。"""
        # expected = round(80*0.5 + 80*0.2 + 80*0.1 + 80*0.1 + 80*0.1) = 80
        assert _validate_match(_make_match(80)) == []

    def test_weighted_score_correct(self):
        """非均匀维度加权正确。"""
        # skill=90, project=60, exp=50, edu=40, eng=30
        # expected = round(90*0.5 + 60*0.2 + 50*0.1 + 40*0.1 + 30*0.1) = round(45+12+5+4+3) = 69
        match = _make_match(69, skill=90, project=60, experience=50, education=40, engineering=30)
        assert _validate_match(match) == []

    def test_inconsistent_score_detected(self):
        """score 与加权结果偏差 > tolerance → 报错。"""
        # expected = 80, score = 50 → 偏差 30
        errors = _validate_match(_make_match(50))
        assert any("不一致" in e for e in errors)

    def test_tolerance_boundary_plus_1(self):
        """score = expected + 1 → 在容差内，无错误。"""
        # expected = 80, score = 81 → ok
        assert _validate_match(_make_match(81)) == []

    def test_tolerance_boundary_minus_1(self):
        """score = expected - 1 → 在容差内，无错误。"""
        # expected = 80, score = 79 → ok
        assert _validate_match(_make_match(79)) == []

    def test_tolerance_exceeded_plus_2(self):
        """score = expected + 2 → 超出容差，报错。"""
        # expected = 80, score = 82 → 偏差 2
        errors = _validate_match(_make_match(82))
        assert any("不一致" in e for e in errors)

    def test_zero_dimensions(self):
        """全零维度 → expected=0。"""
        match = _make_match(
            0, skill=0, project=0, experience=0, education=0, engineering=0
        )
        assert _validate_match(match) == []

    def test_hundred_dimensions(self):
        """满分维度 → expected=100。"""
        match = _make_match(
            100, skill=100, project=100, experience=100, education=100, engineering=100
        )
        assert _validate_match(match) == []

    def test_direct_consistency_function(self):
        """直接测 _validate_score_dimensions_consistency。"""
        dims = {"skill": 90, "project": 80, "experience": 70, "education": 60, "engineering": 50}
        # expected = round(90*0.5 + 80*0.2 + 70*0.1 + 60*0.1 + 50*0.1) = round(45+16+7+6+5) = 79
        assert _validate_score_dimensions_consistency(79, dims) == []
        assert len(_validate_score_dimensions_consistency(50, dims)) == 1

    def test_missing_dim_skips_consistency(self):
        """维度缺失时一致性检查被跳过（由结构校验报错）。"""
        match = {"score": 80, "dimensions": {"skill": 80}, "skill_gap": [], "interpretation": "x"}
        errors = _validate_match(match)
        assert any("缺少键" in e for e in errors)
        # 不应有 score 一致性错误
        assert not any("不一致" in e for e in errors)


# ---------- 非法 match 格式（回归） ----------


class TestInvalidMatchFormats:
    def test_score_out_of_range(self):
        """score 超出 0~100 → 报错。"""
        errors = _validate_match(_make_match(150))
        assert any("score" in e for e in errors)

    def test_score_bool(self):
        """score 为 bool → 报错。"""
        match = _make_match(80)
        match["score"] = True
        errors = _validate_match(match)
        assert any("score" in e for e in errors)

    def test_dimensions_not_dict(self):
        """dimensions 非 dict → 报错。"""
        match = _make_match(80)
        match["dimensions"] = [1, 2, 3]
        errors = _validate_match(match)
        assert any("dimensions" in e for e in errors)

    def test_dimension_value_out_of_range(self):
        """维度值超出 0~100 → 报错。"""
        errors = _validate_match(_make_match(80, skill=200))
        assert any("skill" in e for e in errors)

    def test_skill_gap_not_list(self):
        """skill_gap 非 list → 报错。"""
        match = _make_match(80)
        match["skill_gap"] = "Docker"
        errors = _validate_match(match)
        assert any("skill_gap" in e for e in errors)

    def test_interpretation_not_str(self):
        """interpretation 非 str → 报错。"""
        match = _make_match(80)
        match["interpretation"] = 123
        errors = _validate_match(match)
        assert any("interpretation" in e for e in errors)


# ---------- run() 集成 ----------


async def _noop_emit(event: dict) -> None:
    pass


def _run_agent(state: TaskState):
    agent = ValidatorAgent()
    return asyncio.run(agent.run(state, _noop_emit))


class TestRunReportBranch:
    def test_valid_report_passes(self):
        """合法 report_synthesizer 结果 → status=ok。"""
        state = TaskState()
        state.results["report_synthesizer"] = {
            "report": {"title": "匹配报告", "summary": "综合匹配度 85 分"},
            "source": "template",
            "elapsed_ms": 10,
        }
        result = _run_agent(state)
        assert result.status == "ok"
        assert result.message_type == "validation_result"

    def test_valid_report_with_match_passes(self):
        """合法 report + 合法 match → status=ok。"""
        state = TaskState()
        state.results["report_synthesizer"] = {
            "report": {"title": "匹配报告", "summary": "综合匹配度 80 分"},
            "source": "llm",
            "elapsed_ms": 5,
        }
        state.results["match_agent"] = _make_match(80)
        result = _run_agent(state)
        assert result.status == "ok"

    def test_valid_report_with_inconsistent_match_fails(self):
        """report 合法但 match score 不一致 → status=error。"""
        state = TaskState()
        state.results["report_synthesizer"] = {
            "report": {"title": "T", "summary": "S"},
            "source": "template",
            "elapsed_ms": 0,
        }
        state.results["match_agent"] = _make_match(10)  # expected=80
        result = _run_agent(state)
        assert result.status == "error"
        assert any("不一致" in e for e in result.errors)

    def test_invalid_report_fails(self):
        """report title 为空 → status=error。"""
        state = TaskState()
        state.results["report_synthesizer"] = {
            "report": {"title": "", "summary": "S"},
            "source": "template",
            "elapsed_ms": 0,
        }
        result = _run_agent(state)
        assert result.status == "error"
        assert any("title" in e for e in result.errors)

    def test_invalid_source_fails(self):
        """report source 非法 → status=error。"""
        state = TaskState()
        state.results["report_synthesizer"] = {
            "report": {"title": "T", "summary": "S"},
            "source": "bad",
            "elapsed_ms": 0,
        }
        result = _run_agent(state)
        assert result.status == "error"
        assert any("source" in e for e in result.errors)


class TestRunMatchBranch:
    def test_valid_match_passes(self):
        """合法 match（无 report）→ status=ok。"""
        state = TaskState()
        state.results["match_agent"] = _make_match(80)
        result = _run_agent(state)
        assert result.status == "ok"

    def test_inconsistent_match_fails(self):
        """match score 与 dimensions 不一致 → status=error。"""
        state = TaskState()
        state.results["match_agent"] = _make_match(50)  # expected=80
        result = _run_agent(state)
        assert result.status == "error"
        assert any("不一致" in e for e in result.errors)


class TestRunDataBranch:
    def test_empty_results_falls_to_data(self):
        """无 report/match → 走 data 校验（缺 final 报错）。"""
        state = TaskState()
        result = _run_agent(state)
        assert result.status == "error"
        assert any("final" in e for e in result.errors)

    def test_valid_data_passes(self):
        """合法 data_agent final 结构 → status=ok。"""
        state = TaskState()
        state.results["data_agent"] = {
            "final": {
                "columns": ["a", "b"],
                "rows": [[1, 2], [3, 4]],
                "engine": "duckdb",
                "truncated": False,
            }
        }
        result = _run_agent(state)
        assert result.status == "ok"
