"""Redis 缓存助手：连接失败/超时即降级为 None（契约降级原则）。"""
from __future__ import annotations

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
