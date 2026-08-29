"""引擎路由单测（契约 §3.8/§14）。

choose_engine(profile)：rows_estimate >= settings.SPARK_ROW_THRESHOLD → "spark"，否则 "duckdb"。
"""
import pytest

from app.core.config import settings
from app.data_engine.profiler import DatasetProfile
from app.data_engine.router import choose_engine


def _profile(rows: int) -> DatasetProfile:
    return DatasetProfile(
        path="x.csv",
        size_bytes=1024,
        size_mb=0.001,
        columns=["a"],
        rows_estimate=rows,
    )


def test_small_dataset_goes_duckdb():
    """默认阈值 100000，1 万行走 DuckDB。"""
    assert choose_engine(_profile(10000)) == "duckdb"


def test_large_dataset_goes_spark():
    """20 万行（jd_large.csv 规模）走 Spark。"""
    assert choose_engine(_profile(200000)) == "spark"


def test_threshold_boundary_is_inclusive(monkeypatch):
    monkeypatch.setattr(settings, "SPARK_ROW_THRESHOLD", 1000)
    assert choose_engine(_profile(1000)) == "spark"
    assert choose_engine(_profile(999)) == "duckdb"


def test_monkeypatched_small_threshold(monkeypatch):
    """阈值可由配置调节，不写死。"""
    monkeypatch.setattr(settings, "SPARK_ROW_THRESHOLD", 100)
    assert choose_engine(_profile(200)) == "spark"
    assert choose_engine(_profile(50)) == "duckdb"


def test_default_threshold_value():
    """settings 默认阈值为 100000（与 .env.example 一致）。"""
    assert settings.SPARK_ROW_THRESHOLD == 100000
