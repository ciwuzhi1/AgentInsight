"""EngineResult：引擎执行结果共享契约（CODE-2/CODE-3 共用，定义不得改动）。"""

from dataclasses import dataclass


@dataclass
class EngineResult:
    columns: list[str]
    rows: list[list]          # 已截断
    row_count: int            # 截断后行数
    total_rows: int | None    # 未截断前总数，未知为 None
    truncated: bool
    elapsed_ms: int
    engine: str               # "duckdb" | "spark"
