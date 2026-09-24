"""进程内滑动窗口限流（企业级：防暴力破解与突发滥用）。

单实例内存限流；多实例部署时换 Redis ZSET 实现（接口不变）。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """进程内滑动窗口限流器（线程安全）。"""

    def __init__(self, max_calls: int, window_s: float) -> None:
        self.max_calls = max_calls
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """返回 (是否放行, 建议等待秒数)。"""
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] > self.window_s:
                dq.popleft()
            if len(dq) >= self.max_calls:
                retry_after = max(0.0, self.window_s - (now - dq[0]))
                return False, round(retry_after, 1)
            dq.append(now)
            # 顺手清理空桶，防内存增长
            if len(self._hits) > 10000:
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True, 0.0


# 全局实例：登录/注册 5 次/分钟/IP；任务创建 30 次/分钟/用户
auth_limiter = RateLimiter(max_calls=5, window_s=60)
task_limiter = RateLimiter(max_calls=30, window_s=60)


class RedisRateLimiter:
    """基于 Redis ZSET 的分布式限流（多实例共享额度）；Redis 不可用时自动降级放行。"""

    def __init__(self, max_calls: int, window_s: float, get_client) -> None:
        """get_client 为零参异步函数，返回 redis.asyncio 客户端（或 None）。"""
        self.max_calls = max_calls
        self.window_s = window_s
        self._get_client = get_client  # 延迟获取 redis.asyncio 客户端的零参函数

    async def allow(self, key: str) -> tuple[bool, float]:
        client = None
        try:
            client = await self._get_client()
            if client is None:  # Redis 降级：放行（可用性优先，与缓存同一原则）
                return True, 0.0
            now_ms = int(time.time() * 1000)
            window_ms = int(self.window_s * 1000)
            zkey = f"ratelimit:{key}"
            pipe = client.pipeline()
            pipe.zremrangebyscore(zkey, 0, now_ms - window_ms)
            pipe.zcard(zkey)
            _, count = await pipe.execute()
            if count >= self.max_calls:
                oldest = await client.zrange(zkey, 0, 0, withscores=True)
                retry_after = max(0.0, self.window_s - (now_ms / 1000 - oldest[0][1] / 1000)) if oldest else self.window_s
                return False, round(retry_after, 1)
            pipe = client.pipeline()
            pipe.zadd(zkey, {str(now_ms): now_ms})
            pipe.expire(zkey, int(self.window_s) + 1)
            await pipe.execute()
            return True, 0.0
        except Exception as exc:
            logger.warning("RedisRateLimiter 故障降级放行 key=%s: %s", key, exc)
            return True, 0.0


def build_limiters() -> tuple:
    """工厂：REDIS_RATE_LIMIT=true 且 Redis 可用 → 分布式限流；否则进程内。

    返回 (auth_limiter, task_limiter) 二元组。
    """
    import os
    from app.cache.redis import get_redis

    if os.getenv("REDIS_RATE_LIMIT", "").lower() in ("1", "true", "yes"):
        async def _client():
            return await get_redis()
        distributed = RedisRateLimiter(max_calls=5, window_s=60, get_client=_client)
        distributed_task = RedisRateLimiter(max_calls=30, window_s=60, get_client=_client)
        return distributed, distributed_task
    return auth_limiter, task_limiter
