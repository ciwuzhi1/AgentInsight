"""引擎路由：按数据行数与配置阈值选择 DuckDB 或 Spark。"""

from app.core.config import settings
from app.data_engine.profiler import DatasetProfile


def choose_engine(profile: DatasetProfile) -> str:
    """行数或综合评分达到阈值走 Spark，否则 DuckDB。"""
    if profile.rows_estimate >= settings.SPARK_ROW_THRESHOLD:
        return "spark"
    # 综合评分：行数 × 列数因子；宽表/中等行数也可能触发 Spark
    score = profile.rows_estimate * max(1, len(profile.columns)) / 1000
    if score >= settings.SPARK_SCORE_THRESHOLD:
        return "spark"
    return "duckdb"
