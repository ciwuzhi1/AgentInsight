"""健康检查 API（契约 §8）。"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException

from app.cache.redis import get_redis
from app.persistence.mysql import get_connection

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/mysql")
async def health_mysql() -> dict:
    """SELECT 1 探活；失败返回 503。"""

    def _ping() -> int:
        t0 = time.perf_counter()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
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
    try:
        await client.aclose()
    except Exception:
        pass
    return {"redis": "up"}
