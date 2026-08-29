"""AnalysisEngine 抽象基类（契约 §3.6，CODE-2）。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.data_engine.result import EngineResult


class AnalysisEngine(ABC):
    """分析引擎统一接口：注册数据集、查 schema、执行只读 SQL。"""

    name: str

    @abstractmethod
    def register_dataset(self, dataset_id: str, name: str, path: str) -> dict:
        """注册数据集（如建视图），返回 schema（[{"name","type"}]）。"""

    @abstractmethod
    def get_schema(self, dataset_id: str) -> list[dict]:
        """返回数据集 schema：[{"name": 列名, "type": 类型}]。"""

    @abstractmethod
    async def execute(self, dataset_id: str, sql: str) -> EngineResult:
        """执行只读 SQL，返回 EngineResult。"""
