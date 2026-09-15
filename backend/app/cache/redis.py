"""Redis 缓存助手：单例连接 + 失败降级（契约降级原则）。

CONTRACTS3 §1.3：get_json/set_json 与幂等锁辅助；任何异常一律静默降级，
绝不向调用方抛出（缓存只是加速层，不得拖垮主链路）。

单例优化：进程内共享一个连接，避免每次操作新建 TCP + ping。
连接失败后进入冷却期（5s），冷却期内直接返回 None 不再尝试连接。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 连接超时（秒），Redis 只是缓存层，不应拖慢主链路
_CONNECT_TIMEOUT = 1.0
# 连接失败后的冷却期（秒），避免频繁重试拖慢主链路
_COOLDOWN_S = 5.0

# 单例状态
_client = None  # redis.asyncio.Redis | None
_client_lock: asyncio.Lock | None = None
_last_fail_ts: float = 0.0
_fail_count: int = 0


def _get_lock() -> asyncio.Lock:
    """懒初始化 asyncio.Lock（需在事件循环内调用）。"""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_redis():
    """返回进程内共享的 redis.asyncio.Redis 客户端；失败/冷却期返回 None。"""
    global _client, _last_fail_ts, _fail_count

    # 冷却期内不再尝试连接
    if _fail_count > 0 and time.monotonic() - _last_fail_ts < _COOLDOWN_S:
        return None

    # 已有连接则直接返回
    if _client is not None:
        try:
            # 轻量探活：不每次 ping，用 is_closed 判断
            if not _client.is_closed():
                return _client
        except Exception:
            pass
        # 连接已关闭，重置
        _client = None

    # 懒初始化 + 锁保护，避免并发创建多个连接
    async with _get_lock():
        # double-check
        if _client is not None and not _client.is_closed():
            return _client

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
            _client = client
            _fail_count = 0
            logger.debug("Redis 连接已建立")
            return client
        except Exception as exc:
            _fail_count += 1
            _last_fail_ts = time.monotonic()
            logger.warning("Redis 连接失败（第 %d 次），冷却 %.0fs: %s", _fail_count, _COOLDOWN_S, exc)
            return None


async def close_redis() -> None:
    """关闭单例连接（lifespan shutdown 调用）。"""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None


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


async def cleanup_stale_locks(prefix: str = "agent:lock:") -> int:
    """启动时清理残留的幂等锁（重启后锁可能仍持有，阻止用户重新提交）。

    返回清理的键数量；Redis 不可用返回 0。
    """
    try:
        client = await get_redis()
        if client is None:
            return 0
        keys = []
        async for key in client.scan_iter(match=f"{prefix}*", count=100):
            keys.append(key)
        if keys:
            await client.delete(*keys)
            logger.info("清理残留幂等锁 %d 个", len(keys))
        return len(keys)
    except Exception as exc:
        logger.warning("清理残留锁失败: %s", exc)
        return 0
