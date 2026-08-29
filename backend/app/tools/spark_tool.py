"""Spark 工具：供 data_agent 调用的异步薄封装。"""

import asyncio
import time
from pathlib import Path

from app.data_engine.result import EngineResult
from app.data_engine.spark_engine import run_jd_skill_stats


async def run_skill_stats(csv_path: str | Path, out_name: str | None = None) -> EngineResult:
    """异步执行 Spark 技能统计：阻塞的 subprocess 放线程池，避免卡事件循环。"""
    if out_name is None:
        out_name = f"jd_stats_{time.strftime('%Y%m%d_%H%M%S')}"
    return await asyncio.to_thread(run_jd_skill_stats, Path(csv_path), out_name)
