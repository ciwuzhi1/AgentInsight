"""DuckDB 引擎：进程内把 CSV 注册为视图并执行只读 SQL（契约 §3.6）。

视图命名约定：ds_{dataset_id 前 8 位}，与 CODE-4 API 层 table_name_for 一致。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import duckdb

from app.core.config import settings
from app.core.logging import get_logger
from app.data_engine.base import AnalysisEngine
from app.data_engine.result import EngineResult

logger = get_logger(__name__)


class EngineError(Exception):
    """DuckDB 注册或执行失败。"""


def _posix(path: str) -> str:
    """路径统一为正斜杠（read_csv_auto 需要）。"""
    return str(Path(path)).replace("\\", "/")


def _escape(path: str) -> str:
    """SQL 字符串字面量内的单引号转义。"""
    return path.replace("'", "''")


def table_for(dataset_id: str) -> str:
    """数据集对应的 DuckDB 视图名。"""
    return f"ds_{dataset_id[:8]}"


def _sql_timeout() -> float:
    """SQL 超时秒数：优先读 app_settings 的 sql_timeout（A4 交付），缺省 30。"""
    try:
        from app.core.app_settings import get_setting
    except ImportError:
        return 30.0
    try:
        return max(1.0, float(get_setting("sql_timeout", "30")))
    except Exception:
        return 30.0


class DuckDBEngine(AnalysisEngine):
    """每个 dataset_id 一个独立连接，LRU 淘汰防止内存泄漏。

    最多保留 _MAX_CONNS 个活跃连接，超出时淘汰最久未用的并 close。
    """

    name = "duckdb"
    _MAX_CONNS = 10  # 最大并发连接数

    def __init__(self) -> None:
        self._conns: dict[str, duckdb.DuckDBPyConnection] = {}
        self._tables: dict[str, str] = {}
        self._last_used: dict[str, float] = {}  # dataset_id -> monotonic timestamp
        self._meta: dict[str, tuple[str, float]] = {}  # dataset_id -> (path, mtime)

    def _evict_lru(self) -> None:
        """淘汰最久未用的连接，直到数量低于 _MAX_CONNS。"""
        while len(self._conns) >= self._MAX_CONNS:
            # 找最久未用的
            oldest_id = min(self._last_used, key=self._last_used.get)
            self._close_conn(oldest_id)
            logger.info("DuckDB LRU 淘汰 dataset=%s（活跃连接 %d）", oldest_id, len(self._conns))

    def _close_conn(self, dataset_id: str) -> None:
        """关闭并移除指定连接。"""
        conn = self._conns.pop(dataset_id, None)
        self._tables.pop(dataset_id, None)
        self._last_used.pop(dataset_id, None)
        self._meta.pop(dataset_id, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _conn(self, dataset_id: str) -> duckdb.DuckDBPyConnection:
        conn = self._conns.get(dataset_id)
        if conn is not None:
            self._last_used[dataset_id] = time.monotonic()
            return conn
        # 新建前先淘汰
        self._evict_lru()
        conn = duckdb.connect()
        # 资源上限（CONTRACTS2 §5）：限制内存与线程防止单连接吃满宿主机
        conn.execute("SET memory_limit='1GB'")
        conn.execute("SET threads=4")
        self._conns[dataset_id] = conn
        self._last_used[dataset_id] = time.monotonic()
        return conn

    def close_all(self) -> None:
        """关闭所有连接（lifespan shutdown 调用）。"""
        for dataset_id in list(self._conns.keys()):
            self._close_conn(dataset_id)
        logger.info("DuckDB 所有连接已关闭")

    def connection_count(self) -> int:
        """当前活跃连接数（健康检查用）。"""
        return len(self._conns)

    def unregister_dataset(self, dataset_id: str) -> None:
        """注销数据集：DROP VIEW + 关闭并移除连接（删除数据集时调用，幂等）。"""
        table = self._tables.get(dataset_id) or table_for(dataset_id)
        conn = self._conns.get(dataset_id)
        if conn is not None:
            try:
                conn.execute(f"DROP VIEW IF EXISTS {table}")
            except Exception as exc:
                logger.warning("DuckDB 视图清理失败 dataset=%s table=%s: %s", dataset_id, table, exc)
        self._close_conn(dataset_id)
        logger.info("数据集已注销 dataset=%s table=%s", dataset_id, table)

    def register_dataset(self, dataset_id: str, name: str, path: str) -> dict:
        """把 CSV 注册为视图并返回 schema；path/mtime 未变时复用缓存视图。"""
        table = table_for(dataset_id)
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            mtime = -1.0
        cached = self._meta.get(dataset_id)
        if dataset_id in self._tables and cached == (path, mtime):
            logger.debug("视图未变更，跳过重建 dataset=%s table=%s", dataset_id, table)
            return self.get_schema(dataset_id)
        sql = (
            f"CREATE OR REPLACE VIEW {table} AS "
            f"SELECT * FROM read_csv_auto('{_escape(_posix(path))}', header=true)"
        )
        try:
            conn = self._conn(dataset_id)
            conn.execute(sql)
        except Exception as exc:
            raise EngineError(f"注册数据集失败 {dataset_id}: {exc}") from exc
        self._tables[dataset_id] = table
        self._meta[dataset_id] = (path, mtime)
        logger.info("数据集已注册 dataset=%s table=%s path=%s", dataset_id, table, path)
        return self.get_schema(dataset_id)

    def get_schema(self, dataset_id: str) -> list[dict]:
        """DESCRIBE 视图得到 [{"name","type"}]。"""
        table = self._tables.get(dataset_id) or table_for(dataset_id)
        conn = self._conns.get(dataset_id)
        if conn is None:
            raise EngineError(f"数据集未注册: {dataset_id}")
        try:
            rows = conn.execute(f"DESCRIBE {table}").fetchall()
        except Exception as exc:
            raise EngineError(f"读取 schema 失败 {dataset_id}: {exc}") from exc
        return [{"name": r[0], "type": r[1]} for r in rows]

    async def execute(self, dataset_id: str, sql: str) -> EngineResult:
        """异步执行 SQL：wait_for 超时（默认 30s），超时同时对底层连接 interrupt() 硬中断。"""
        timeout = _sql_timeout()
        conn = self._conns.get(dataset_id)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_sync, dataset_id, sql), timeout
            )
        except asyncio.TimeoutError:
            if conn is not None:
                try:
                    conn.interrupt()  # 让后台线程的查询尽快中止，而不是继续空烧 CPU
                except Exception:
                    pass
            raise EngineError("查询超时") from None

    def _execute_sync(self, dataset_id: str, sql: str) -> EngineResult:
        conn = self._conns.get(dataset_id)
        if conn is None:
            raise EngineError(f"数据集未注册: {dataset_id}")
        t0 = time.perf_counter()
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(settings.SQL_MAX_ROWS + 1)
        except Exception as exc:
            raise EngineError(f"SQL 执行失败: {exc}") from exc
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        truncated = len(rows) > settings.SQL_MAX_ROWS
        rows = [list(r) for r in rows[: settings.SQL_MAX_ROWS]]
        # 未截断时行数即总数；截断时总数未知
        total_rows = len(rows) if not truncated else None
        return EngineResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            total_rows=total_rows,
            truncated=truncated,
            elapsed_ms=elapsed_ms,
            engine="duckdb",
        )


# 模块级单例
duckdb_engine = DuckDBEngine()
