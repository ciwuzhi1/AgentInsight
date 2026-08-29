"""引擎路由：按数据行数与配置阈值选择 DuckDB 或 Spark。"""

from app.core.config import settings
from app.data_engine.profiler import DatasetProfile


def choose_engine(profile: DatasetProfile) -> str:
    """行数达到 SPARK_ROW_THRESHOLD 走 Spark，否则 DuckDB。"""
    # 预留：后续可按查询复杂度扩展路由规则
    if profile.rows_estimate >= settings.SPARK_ROW_THRESHOLD:
        return "spark"
    return "duckdb"
