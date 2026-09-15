"""DataAgent：画像 → 引擎路由 → NL2SQL/Spark → 组装 final_result（契约 §4/§6）。"""
from __future__ import annotations

import asyncio
import datetime
import json
import re
import time
from decimal import Decimal

from app.agents.base import AgentResult, BaseAgent, EmitFn
from app.agent_runtime.state import TaskState
from app.cache.keys import nl2sql_key, text_hash
from app.cache.policies import TTL_SCHEMA
from app.cache.redis import delete_key, get_json, set_json
from app.core.config import settings
from app.core.llm import NL2SQL_SYSTEM_PROMPT, get_llm_client
from app.data_engine.duckdb_engine import EngineError, duckdb_engine
from app.data_engine.profiler import profile_csv
from app.data_engine.router import choose_engine
from app.tools.schema_tool import format_schema_for_prompt
from app.tools.spark_tool import run_skill_stats
from app.tools.sql_tool import SQLGuardError, guard

# 日期字符串前缀（如 2025-01-29 / 2025-01）
_DATE_RE = re.compile(r"^\d{4}-\d{1,2}(-\d{1,2})?")


async def _delete_cache(key: str) -> None:
    """删除缓存键（坏缓存失效）；失败仅告警不阻断。"""
    try:
        await delete_key(key)
    except Exception:
        pass


def _is_date(v: object) -> bool:
    """判断值是否为日期（date/datetime 或 ISO 日期字符串）。"""
    if isinstance(v, (datetime.date, datetime.datetime)):
        return True
    return isinstance(v, str) and bool(_DATE_RE.match(v))


def _is_number(v: object) -> bool:
    """判断值是否为数值（bool 不算）。"""
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def build_chart(columns: list[str], rows: list[list]) -> dict | None:
    """按契约 §6 规则构造图表配置；构不成图返回 None。

    第一列为字符串/日期列作 x_field，其余数值列作 y_fields（最多 3 个）；
    x 为日期列 → line，否则 bar。类型检测取各列前 5 个非空值，避免首行空值丢图。
    """
    if not columns or not rows or len(columns) < 2:
        return None

    def _sample_non_null(col_idx: int, limit: int = 5) -> list:
        """取该列前 limit 个非空值用于类型判断。"""
        vals: list = []
        for row in rows:
            if col_idx < len(row) and row[col_idx] is not None:
                vals.append(row[col_idx])
                if len(vals) >= limit:
                    break
        return vals

    x_samples = _sample_non_null(0)
    if not x_samples:
        return None
    x_value = x_samples[0]
    if not (_is_date(x_value) or isinstance(x_value, str)):
        return None

    y_fields: list[str] = []
    for i in range(1, min(len(columns), 4)):
        samples = _sample_non_null(i)
        if samples and all(_is_number(v) for v in samples):
            y_fields.append(columns[i])
    if not y_fields:
        return None
    return {
        "type": "line" if _is_date(x_value) else "bar",
        "x_field": columns[0],
        "y_fields": y_fields,
    }


class DataAgent(BaseAgent):
    """数据分析主 agent。"""

    name = "data_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        t0 = time.perf_counter()
        ds = state.context.get("dataset") or {}
        path = ds.get("path")
        if not path:
            return AgentResult(
                status="error",
                message_type="data_result",
                data={},
                errors=["缺少数据集元数据 state.context['dataset']"],
            )

        # 1. 画像 + 引擎路由
        profile = await asyncio.to_thread(profile_csv, path)
        engine = choose_engine(profile)
        await emit(
            {
                "type": "engine",
                "engine": engine,
                "rows_estimate": profile.rows_estimate,
                "reason": (
                    f"行数 {profile.rows_estimate} "
                    + (
                        f">= 阈值 {settings.SPARK_ROW_THRESHOLD}，走 Spark"
                        if engine == "spark"
                        else f"低于阈值 {settings.SPARK_ROW_THRESHOLD}，走 DuckDB"
                    )
                ),
            }
        )

        sql: str | None = None
        explanation = "mock 规则生成"
        cache_state = None
        if engine == "spark":
            # Spark 分支：直接跑技能统计任务，不经 guard/NL2SQL
            result = await run_skill_stats(path)
            explanation = "数据量较大，已路由到 Spark 执行技能统计任务"
        else:
            # DuckDB 分支：注册视图 → schema → NL2SQL（缓存）→ guard → 执行
            name = ds.get("name") or ""
            schema = await asyncio.to_thread(
                duckdb_engine.register_dataset, state.dataset_id or "", name, path
            )
            table = ds.get("table_name") or f"ds_{(state.dataset_id or '')[:8]}"
            schema_str = format_schema_for_prompt(schema)
            schema_hash = text_hash(schema_str)
            cache_key = nl2sql_key(state.dataset_id or "", schema_hash, state.query)

            # NL2SQL 缓存：相同 (dataset, schema, query) 复用 SQL，不调 LLM
            cached = await get_json(cache_key)
            if cached and cached.get("sql"):
                raw_sql = str(cached["sql"]).strip()
                explanation = str(cached.get("explanation") or "缓存命中")
                cache_state = "hit"
            else:
                cache_state = "miss"
                user_prompt = (
                    f"表名: {table}\n"
                    f"字段:\n{schema_str}\n"
                    f"用户问题: {state.query}\n"
                )
                llm_result = await get_llm_client().generate_json(NL2SQL_SYSTEM_PROMPT, user_prompt)
                raw_sql = str(llm_result.get("sql") or "").strip()
                explanation = str(llm_result.get("explanation") or "mock 规则生成")

            ok, clean_sql, reason = guard(raw_sql, settings.SQL_MAX_ROWS)
            if not ok:
                raise SQLGuardError(f"SQL 未通过安全校验: {reason}")
            sql = clean_sql
            await emit({"type": "sql", "sql": sql, "explanation": explanation})

            # 执行 + 错误反馈重试（最多 2 次）
            result = None
            last_err = None
            for attempt in range(3):  # 1 次原始 + 2 次重试
                try:
                    result = await duckdb_engine.execute(state.dataset_id or "", sql)
                    break
                except Exception as exc:
                    last_err = exc
                    # 缓存命中但执行失败：删除坏缓存，下次重新生成
                    if cache_state == "hit":
                        await _delete_cache(cache_key)
                        cache_state = "miss"  # 允许重试
                    if attempt < 2 and cache_state == "miss":
                        # 把错误喂回 LLM 重新生成 SQL
                        retry_prompt = (
                            f"表名: {table}\n"
                            f"字段:\n{schema_str}\n"
                            f"用户问题: {state.query}\n"
                            f"上一次生成的 SQL 执行失败：{exc}\n"
                            f"上一次的 SQL：{sql}\n"
                            f"请修正 SQL 后重新输出。"
                        )
                        try:
                            llm_result = await get_llm_client().generate_json(
                                NL2SQL_SYSTEM_PROMPT, retry_prompt
                            )
                            new_sql = str(llm_result.get("sql") or "").strip()
                            ok, clean_sql, reason = guard(new_sql, settings.SQL_MAX_ROWS)
                            if ok:
                                sql = clean_sql
                                explanation = str(llm_result.get("explanation") or explanation)
                                await emit({"type": "sql", "sql": sql, "explanation": f"重试修正：{explanation}"})
                                continue
                        except Exception:
                            pass
                    raise
            if result is None:
                raise last_err or EngineError("SQL 执行失败")

            # 写缓存（仅首次生成成功时写入）
            if cache_state == "miss" and sql:
                await set_json(cache_key, {"sql": sql, "explanation": explanation}, TTL_SCHEMA)

        # 组装 §6 final_result
        final = {
            "task_id": state.task_id,
            "query": state.query,
            "engine": engine,
            "sql": sql,
            "explanation": explanation,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "chart": build_chart(result.columns, result.rows),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
        data = {
            "engine": engine,
            "sql": sql,
            "explanation": explanation,
            "final": final,
        }
        state.results["data_agent"] = data
        return AgentResult(status="ok", message_type="data_result", data=data)
