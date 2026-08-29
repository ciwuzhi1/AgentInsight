"""Schema 工具：取引擎 schema 并格式化为 LLM prompt 片段（契约 §4）。"""
from __future__ import annotations

from app.data_engine.base import AnalysisEngine


def get_schema(engine: AnalysisEngine, dataset_id: str) -> list[dict]:
    """薄封装：从引擎读取数据集 schema（[{"name","type"}]）。"""
    return engine.get_schema(dataset_id)


def format_schema_for_prompt(schema: list[dict]) -> str:
    """把 schema 格式化为 prompt 里的字段清单，一行一列。"""
    return "\n".join(f"- {c.get('name')}: {c.get('type')}" for c in schema)
