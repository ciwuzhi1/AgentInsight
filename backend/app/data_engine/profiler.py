"""CSV 数据集画像：文件大小、列名、行数估算。"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

# 行数精确统计上限：不超过该大小直接数换行
_EXACT_LIMIT_BYTES = 5 * 1024 * 1024
# 估算时读取的头部样本大小
_SAMPLE_BYTES = 64 * 1024


@dataclass
class DatasetProfile:
    path: str
    size_bytes: int
    size_mb: float
    columns: list[str] = field(default_factory=list)
    rows_estimate: int = 0
    format: str = "csv"


def _count_lines(path: Path) -> int:
    """二进制流式统计换行符数（含表头行）。"""
    with path.open("rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))


def _estimate_lines(path: Path, size_bytes: int) -> int:
    """大文件行数估算：读前 64KB 算平均行长，按文件大小外推。

    末尾可能是不完整的行，故样本先截到最后一个换行符再统计，
    结果为近似值（误差约正负一行）。
    """
    with path.open("rb") as f:
        sample = f.read(_SAMPLE_BYTES)
    # 去掉末尾被截断的半行
    sample = sample.rsplit(b"\n", 1)[0]
    if not sample:
        return 0
    line_count = sample.count(b"\n") + 1
    avg_len = len(sample) / line_count
    return int(size_bytes / avg_len)


def profile_csv(path: str | Path) -> DatasetProfile:
    """生成 CSV 数据集画像。行数：≤5MB 精确计数换行，否则抽样外推。"""
    p = Path(path)
    size_bytes = p.stat().st_size

    # 列名取首行，csv.reader 正确处理引号内的逗号
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        columns = next(csv.reader(f), [])

    if size_bytes <= _EXACT_LIMIT_BYTES:
        lines = _count_lines(p)
    else:
        lines = _estimate_lines(p, size_bytes)

    return DatasetProfile(
        path=str(p),
        size_bytes=size_bytes,
        size_mb=round(size_bytes / (1024 * 1024), 2),
        columns=columns,
        # 减去表头行
        rows_estimate=max(lines - 1, 0),
        format="csv",
    )
