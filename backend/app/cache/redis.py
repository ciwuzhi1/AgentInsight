"""Redis 缓存助手：连接失败/超时即降级为 None（契约降级原则）。

CONTRACTS3 §1.3：get_json/set_json 与幂等锁辅助；任何异常一律静默降级，
绝不向调用方抛出（缓存只是加速层，不得拖垮主链路）。
"""
from __future__ import annotations

import json

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 连接超时（秒），Redis 只是缓存层，不应拖慢主链路
_CONNECT_TIMEOUT = 1.0


async def get_redis():
    """返回 redis.asyncio.Redis 客户端；任何失败 warning 后返回 None。"""
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis 包未安装，缓存功能降级")
        return None
    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_CONNECT_TIMEOUT,
            decode_responses=True,
        )
        await client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis 连接失败，缓存功能降级: %s", exc)
        return None


async def get_json(key: str) -> dict | None:
    """读缓存；不存在/坏数据/Redis 不可用一律返回 None（按 MISS 处理）。"""
    try:
        client = await get_redis()
        if client is None:
            return None
        raw = await client.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Redis get_json 降级 key=%s: %s", key, exc)
        return None


async def set_json(key: str, obj: dict, ttl: int) -> bool:
    """写缓存（带 TTL）；Redis 不可用/任何异常静默返回 False。"""
    try:
        client = await get_redis()
        if client is None:
            return False
        await client.set(key, json.dumps(obj, ensure_ascii=False), ex=ttl)
        return True
    except Exception as exc:
        logger.warning("Redis set_json 降级 key=%s: %s", key, exc)
        return False


async def acquire_lock(key: str, value: str, ttl: int = 300) -> tuple[bool, str | None]:
    """幂等锁 SET NX EX（CONTRACTS3 §1.5）。

    返回 (是否获得锁, 已占用者 value)；Redis 不可用/任何异常 →
    (True, None) 降级放行，业务照常执行。
    """
    try:
        client = await get_redis()
        if client is None:
            return True, None
        ok = await client.set(key, value, nx=True, ex=ttl)
        if ok:
            return True, None
        existing = await client.get(key)
        return False, existing
    except Exception as exc:
        logger.warning("Redis acquire_lock 降级 key=%s: %s", key, exc)
        return True, None


async def release_lock(key: str) -> bool:
    """任务终结时 DEL 幂等锁；Redis 不可用静默返回 False。"""
    try:
        client = await get_redis()
        if client is None:
            return False
        await client.delete(key)
        return True
    except Exception as exc:
        logger.warning("Redis release_lock 降级 key=%s: %s", key, exc)
        return False
