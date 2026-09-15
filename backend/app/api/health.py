"""健康检查 API（契约 §8）。"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.cache.redis import get_redis
from app.persistence.mysql import get_connection

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health() -> dict:
    """简易健康探针：进程存活即返回 ok。"""
    return {"status": "ok"}


@router.get("/live")
async def healthz() -> dict:
    """K8s 风格存活探针：进程活着即 200（不探测依赖）。"""
    return {"status": "alive"}


@router.get("/ready")
async def readyz() -> dict:
    """K8s 风格就绪探针：MySQL 必须可用；Redis 允许降级（degraded 不阻塞就绪）。"""

    def _ping() -> int:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return 0

    mysql_ok = True
    try:
        await asyncio.to_thread(_ping)
    except Exception:
        mysql_ok = False
    # Redis 单例：直接检查连接状态，不新建也不关闭
    try:
        redis_client = await get_redis()
    except Exception:
        redis_client = None
    redis_state = "up" if redis_client is not None else "degraded"
    if not mysql_ok:
        return JSONResponse(status_code=503, content={"ready": False, "mysql": "down", "redis": redis_state})
    return {"ready": True, "mysql": "up", "redis": redis_state}


@router.get("/mysql")
async def health_mysql() -> dict:
    """SELECT 1 探活；失败返回 503。"""

    def _ping() -> int:
        t0 = time.perf_counter()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return int((time.perf_counter() - t0) * 1000)

    try:
        latency_ms = await asyncio.to_thread(_ping)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"mysql down: {exc}") from exc
    return {"mysql": "up", "latency_ms": latency_ms}


@router.get("/redis")
async def health_redis() -> dict:
    """Redis 探活；挂了返回 degraded 而非 500（降级原则）。"""
    try:
        client = await get_redis()
    except Exception:
        client = None
    if client is None:
        return {"redis": "degraded"}
    return {"redis": "up"}


@router.get("/duckdb")
async def health_duckdb() -> dict:
    """DuckDB 探活：进程内引擎 + 当前活跃连接数（LRU 上限内）。"""
    try:
        from app.data_engine.duckdb_engine import duckdb_engine
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"duckdb down: {exc}") from exc

    def _ping() -> int:
        import duckdb

        conn = duckdb.connect()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return duckdb_engine.connection_count()

    try:
        connections = await asyncio.to_thread(_ping)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"duckdb down: {exc}") from exc
    return {
        "duckdb": "up",
        "connections": connections,
        "max_connections": duckdb_engine._MAX_CONNS,
    }
