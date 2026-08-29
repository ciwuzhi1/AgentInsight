"""ValidatorAgent：校验 data_agent 的 final_result 结构（契约 §4/§6）。"""
from __future__ import annotations

import time

from app.agents.base import AgentResult, BaseAgent, EmitFn
from app.agent_runtime.state import TaskState
from app.core.config import settings


class ValidatorAgent(BaseAgent):
    """结果结构校验：列名/行数/引擎/truncated/chart。"""

    name = "validator_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        t0 = time.perf_counter()
        data = state.results.get("data_agent")
        final = data.get("final") if isinstance(data, dict) else None

        errors: list[str] = []
        if not isinstance(final, dict):
            errors.append("缺少 final 结果（state.results['data_agent']['final']）")
        else:
            columns = final.get("columns")
            if not isinstance(columns, list) or not columns:
                errors.append("columns 必须为非空 list")

            rows = final.get("rows")
            if not isinstance(rows, list):
                errors.append("rows 必须为 list")
            elif len(rows) > settings.SQL_MAX_ROWS:
                errors.append(
                    f"rows 行数 {len(rows)} 超过上限 {settings.SQL_MAX_ROWS}"
                )

            if final.get("engine") not in {"duckdb", "spark"}:
                errors.append(f"engine 非法: {final.get('engine')!r}")

            if not isinstance(final.get("truncated"), bool):
                errors.append("truncated 必须为 bool")

            chart = final.get("chart")
            if chart is not None and not isinstance(chart, dict):
                errors.append("chart 必须为 dict 或 None")

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
