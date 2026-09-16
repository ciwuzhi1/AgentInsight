"""ValidatorAgent：结果结构校验（CONTRACTS2 §3.4）。

分支：
1. results 含 report_synthesizer → 校验报告结构（title、summary、source）
2. results 含 match_agent → 校验匹配结构（score 0~100 int、dimensions 五键、
   skill_gap list、interpretation str、score-dimensions 一致性）
3. 否则校验 data_agent 的 final_result 结构
"""
from __future__ import annotations

import time

from app.agents.base import AgentResult, BaseAgent, EmitFn
from app.agent_runtime.state import TaskState
from app.core.config import settings

# 匹配维度五键
_MATCH_DIMENSION_KEYS = {"skill", "project", "experience", "education", "engineering"}

# 分项权重（与 match_agent._WEIGHTS 保持一致，用于 score-dimensions 一致性校验）
_MATCH_WEIGHTS = {"skill": 0.5, "project": 0.2, "experience": 0.1, "education": 0.1, "engineering": 0.1}

# score-dimensions 一致性允许的误差（rounding tolerance）
_SCORE_TOLERANCE = 1


def _validate_score_dimensions_consistency(score: int, dimensions: dict) -> list[str]:
    """校验 score 与 dimensions 的加权一致性。

    score 应等于 round(sum(dimensions[k] * weight))，允许 ±1 的 rounding 误差。
    """
    errors: list[str] = []

    # 仅在所有维度键都有合法 int 值时做一致性校验
    valid_dims = {
        k: dimensions[k]
        for k in _MATCH_DIMENSION_KEYS
        if k in dimensions and isinstance(dimensions[k], int) and not isinstance(dimensions[k], bool)
    }
    if len(valid_dims) != len(_MATCH_DIMENSION_KEYS):
        return errors  # 维度缺失或类型不对，已在结构校验中报错

    expected = round(sum(valid_dims[k] * w for k, w in _MATCH_WEIGHTS.items()))
    if abs(score - expected) > _SCORE_TOLERANCE:
        errors.append(
            f"score({score}) 与 dimensions 加权结果({expected}) 不一致，"
            f"允许误差 ±{_SCORE_TOLERANCE}"
        )

    return errors


def _validate_match(match: dict) -> list[str]:
    """校验匹配结果结构，返回错误列表。"""
    errors: list[str] = []

    score = match.get("score")
    # bool 是 int 子类，需排除
    if not isinstance(score, int) or isinstance(score, bool) or not (0 <= score <= 100):
        errors.append(f"score 必须为 0~100 的 int，实际: {score!r}")

    dimensions = match.get("dimensions")
    if not isinstance(dimensions, dict):
        errors.append("dimensions 必须为 dict")
    else:
        missing = _MATCH_DIMENSION_KEYS - set(dimensions)
        if missing:
            errors.append(f"dimensions 缺少键: {sorted(missing)}")
        for key, val in dimensions.items():
            if key in _MATCH_DIMENSION_KEYS and (
                not isinstance(val, int) or isinstance(val, bool)
                or not (0 <= val <= 100)
            ):
                errors.append(f"dimensions[{key}] 必须为 0~100 的 int，实际: {val!r}")

        # score-dimensions 一致性校验（仅当 score 和 dimensions 均合法时）
        if (
            isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100
            and not missing
            and all(
                isinstance(dimensions[k], int) and not isinstance(dimensions[k], bool)
                for k in _MATCH_DIMENSION_KEYS
                if k in dimensions
            )
        ):
            errors.extend(_validate_score_dimensions_consistency(score, dimensions))

    if not isinstance(match.get("skill_gap"), list):
        errors.append("skill_gap 必须为 list")

    if not isinstance(match.get("interpretation"), str):
        errors.append("interpretation 必须为 str")

    return errors


def _validate_report(result: dict | None) -> list[str]:
    """校验 report_synthesizer 输出结构。

    期望结构（来自 report_agent.run()）:
    {
        "report": {"title": str, "summary": str, ...},
        "source": "template" | "llm",
        "elapsed_ms": int,
    }
    """
    if not isinstance(result, dict) or not result:
        return ["缺少 report_synthesizer 结果（state.results['report_synthesizer']）"]

    errors: list[str] = []

    # source 必须为 template 或 llm
    source = result.get("source")
    if source not in {"template", "llm"}:
        errors.append(f"source 必须为 'template' 或 'llm'，实际: {source!r}")

    report = result.get("report")
    if not isinstance(report, dict):
        errors.append("report 必须为 dict")
        return errors

    # title 必须存在且为非空 str
    title = report.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"title 必须为非空 str，实际: {title!r}")

    # summary 必须为非空 str
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"summary 必须为非空 str，实际: {summary!r}")

    return errors


def _validate_data(data: dict | None) -> list[str]:
    """校验数据链路 final_result 结构（原逻辑）。"""
    final = data.get("final") if isinstance(data, dict) else None
    if not isinstance(final, dict):
        return ["缺少 final 结果（state.results['data_agent']['final']）"]

    errors: list[str] = []
    columns = final.get("columns")
    if not isinstance(columns, list) or not columns:
        errors.append("columns 必须为非空 list")

    rows = final.get("rows")
    if not isinstance(rows, list):
        errors.append("rows 必须为 list")
    elif len(rows) > settings.SQL_MAX_ROWS:
        errors.append(f"rows 行数 {len(rows)} 超过上限 {settings.SQL_MAX_ROWS}")

    if final.get("engine") not in {"duckdb"}:
        errors.append(f"engine 非法: {final.get('engine')!r}")

    if not isinstance(final.get("truncated"), bool):
        errors.append("truncated 必须为 bool")

    # 行列形状：前 50 行行宽必须等于列数
    if isinstance(columns, list) and columns and isinstance(rows, list):
        if not all(len(r) == len(columns) for r in rows[:50]):
            errors.append("rows 行宽与 columns 列数不一致")

    chart = final.get("chart")
    if chart is not None and not isinstance(chart, dict):
        errors.append("chart 必须为 dict 或 None")
    elif isinstance(chart, dict):
        ctype = chart.get("type")
        if ctype not in {"line", "bar"}:
            errors.append(f"chart.type 非法: {ctype!r}")
        col_set = set(columns) if isinstance(columns, list) else set()
        if chart.get("x_field") not in col_set:
            errors.append(f"chart.x_field 不在 columns 中: {chart.get('x_field')!r}")
        y_fields = chart.get("y_fields")
        if not isinstance(y_fields, list):
            errors.append("chart.y_fields 必须为 list")
        else:
            for yf in y_fields:
                if yf not in col_set:
                    errors.append(f"chart.y_fields 含未知列: {yf!r}")

    return errors


class ValidatorAgent(BaseAgent):
    """结果结构校验：报告链路 / 匹配链路 / 数据链路三分支。"""

    name = "validator_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        t0 = time.perf_counter()

        report = state.results.get("report_synthesizer")
        match = state.results.get("match_agent")

        if isinstance(report, dict) and report:
            # 优先校验报告（report_synthesizer 在链路末端）
            errors = _validate_report(report)
            # 同时校验 match 结构与 score-dimensions 一致性（如有）
            if isinstance(match, dict) and match:
                errors.extend(_validate_match(match))
        elif isinstance(match, dict) and match:
            errors = _validate_match(match)
        else:
            errors = _validate_data(state.results.get("data_agent"))

        if errors:
            message = "; ".join(errors)
            await emit({"type": "error", "code": "VALIDATION_ERROR", "message": message})
            return AgentResult(
                status="error", message_type="validation_result", data={}, errors=errors
            )

        return AgentResult(
            status="ok",
            message_type="validation_result",
            data={"latency_ms": int((time.perf_counter() - t0) * 1000)},
        )
