"""Spark 引擎：通过 docker 运行 spark-submit 执行岗位技能统计任务。"""

import csv
import json
import subprocess
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.data_engine.result import EngineResult

logger = get_logger(__name__)

SPARK_TIMEOUT_SECONDS = 300
_STDERR_KEEP = 2000


class SparkError(Exception):
    """Spark 任务执行失败，携带 stderr 末尾片段。"""

    def __init__(self, message: str, stderr: str | None = None):
        self.stderr = (stderr or "")[-_STDERR_KEEP:]
        if self.stderr:
            message = f"{message}\nstderr(末尾 {len(self.stderr)} 字符):\n{self.stderr}"
        super().__init__(message)


def _repo_root() -> Path:
    # backend/app/data_engine/spark_engine.py 上三级即仓库根
    return Path(__file__).resolve().parents[3]


def _data_dir() -> Path:
    if settings.DATA_DIR:
        return Path(settings.DATA_DIR)
    return _repo_root() / "data"


def _docker_path(p: Path) -> str:
    # Docker Desktop 卷挂载接受 "D:/xxx" 正斜杠形式
    return p.resolve().as_posix()


def _stderr_of(exc: subprocess.TimeoutExpired) -> str:
    s = exc.stderr or ""
    return s.decode("utf-8", "replace") if isinstance(s, bytes) else s


def _convert(value: str):
    """尽力把 CSV 字符串单元转成数值，便于前端直接作图。"""
    v = value.strip()
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return value


def _read_summary(out_dir: Path) -> dict:
    """读取 summary.json（总行数/耗时）；兼容写在 out_name 目录内或其父目录两种位置。"""
    for candidate in (out_dir / "summary.json", out_dir.parent / "summary.json"):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("summary.json 读取失败: %s", candidate)
    return {}


def _build_command(csv_path: Path, data_dir: Path, out_name: str) -> list[str]:
    rel = csv_path.relative_to(data_dir)
    jobs_dir = _repo_root() / "spark" / "jobs"
    return [
        "docker", "run", "--rm",
        "-v", f"{_docker_path(data_dir)}:/data",
        "-v", f"{_docker_path(jobs_dir)}:/jobs",
        settings.SPARK_IMAGE,
        "spark-submit", "--master", "local[*]", "--driver-memory", "1g",
        "/jobs/jd_skill_stats.py",
        "--input", f"/data/{rel.as_posix()}",
        "--output", f"/data/out/{out_name}",
    ]


def _read_output(out_dir: Path, limit: int) -> tuple[list[str], list[list]]:
    """读取 out 目录下的 part-*.csv（含表头），行数截断到 limit。"""
    part_files = sorted(out_dir.glob("part-*.csv"))
    if not part_files:
        raise SparkError(f"Spark 输出目录缺少 part-*.csv: {out_dir}")
    columns: list[str] = []
    rows: list[list] = []
    for pf in part_files:
        if len(rows) >= limit:
            break
        with pf.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            if not columns:
                columns = header
            for row in reader:
                if len(rows) >= limit:
                    break
                rows.append([_convert(c) for c in row])
    return columns, rows


def run_jd_skill_stats(csv_path: Path, out_name: str) -> EngineResult:
    """同步执行 Spark 任务（阻塞，调用方负责 asyncio.to_thread）。"""
    data_dir = _data_dir().resolve()
    csv_path = Path(csv_path).resolve()
    if not csv_path.is_relative_to(data_dir):
        raise SparkError(f"输入 CSV 必须位于 DATA_DIR({data_dir}) 下: {csv_path}")

    cmd = _build_command(csv_path, data_dir, out_name)
    logger.info("执行 Spark 任务: %s", " ".join(cmd))
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=SPARK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SparkError(f"Spark 任务超时({SPARK_TIMEOUT_SECONDS}s)", stderr=_stderr_of(exc)) from exc
    except FileNotFoundError as exc:
        raise SparkError("docker 命令不可用，请确认 Docker Desktop 已启动") from exc
    if proc.returncode != 0:
        raise SparkError(f"Spark 任务失败，退出码 {proc.returncode}", stderr=proc.stderr)

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    out_dir = data_dir / "out" / out_name
    columns, rows = _read_output(out_dir, settings.SQL_MAX_ROWS)

    summary = _read_summary(out_dir)
    total_rows = summary.get("total_rows")
    total_rows = int(total_rows) if total_rows is not None else None
    truncated = total_rows is not None and total_rows > len(rows)

    logger.info("Spark 任务完成: out=%s rows=%d elapsed=%dms", out_name, len(rows), elapsed_ms)
    return EngineResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        total_rows=total_rows,
        truncated=truncated,
        elapsed_ms=elapsed_ms,
        engine="spark",
    )
