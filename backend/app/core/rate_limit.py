"""进程内滑动窗口限流（企业级：防暴力破解与突发滥用）。

单实例内存限流；多实例部署时换 Redis ZSET 实现（接口不变）。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
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
