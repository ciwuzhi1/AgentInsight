"""ValidatorAgent：结果结构校验（CONTRACTS2 §3.4）。

分支：results 含 match_agent → 校验匹配结构（score 0~100 int、dimensions 五键、
skill_gap list、interpretation str）；否则校验 data_agent 的 final_result 结构。
"""
from __future__ import annotations

import time

from app.agents.base import AgentResult, BaseAgent, EmitFn
from app.agent_runtime.state import TaskState
from app.core.config import settings

# 匹配维度五键
_MATCH_DIMENSION_KEYS = {"skill", "project", "experience", "education", "engineering"}


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

    if not isinstance(match.get("skill_gap"), list):
        errors.append("skill_gap 必须为 list")

    if not isinstance(match.get("interpretation"), str):
        errors.append("interpretation 必须为 str")

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

    if final.get("engine") not in {"duckdb", "spark"}:
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
    """结果结构校验：匹配链路 / 数据链路双分支。"""

    name = "validator_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        t0 = time.perf_counter()
        match = state.results.get("match_agent")
        if isinstance(match, dict) and match:
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
