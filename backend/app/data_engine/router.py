"""引擎路由：按数据行数阈值选择 DuckDB 或拒绝（too_large）。"""

from app.core.config import settings
from app.data_engine.profiler import DatasetProfile


def choose_engine(profile: DatasetProfile) -> str:
    """行数达到阈值返回 too_large，否则 DuckDB。"""
    if profile.rows_estimate >= settings.MAX_ROWS_THRESHOLD:
        return "too_large"
    return "duckdb"
