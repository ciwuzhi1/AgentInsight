"""缓存集成测试：Redis cache-aside 模式（FakeRedis mock）。

覆盖：get_json/set_json 往返、命中跳过 LLM、未命中调用后回写、故障降级。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.cache import redis as redis_mod
from app.cache.keys import nl2sql_key, text_hash
from app.cache.policies import TTL_SCHEMA


class FakeRedis:
    """内存版 redis.asyncio 子集：get / set / delete / is_closed / ping。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.fail_get = False
        self.fail_set = False
        self.fail_delete = False
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def ping(self):
        return True

    async def get(self, key: str):
        if self.fail_get:
            raise ConnectionError("FakeRedis get failure")
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value, ex=None, nx: bool = False):
        if self.fail_set:
            raise ConnectionError("FakeRedis set failure")
        self.set_calls += 1
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys):
        if self.fail_delete:
            raise ConnectionError("FakeRedis delete failure")
        removed = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                removed += 1
        return removed

    async def aclose(self):
        self.closed = True


def install_fake_redis(fake: FakeRedis | None = None) -> FakeRedis:
    """把 FakeRedis 注入 redis 模块单例，并重置冷却状态。"""
    fake = fake or FakeRedis()
    redis_mod._client = fake
    redis_mod._fail_count = 0
    redis_mod._last_fail_ts = 0.0
    return fake


def reset_redis_module() -> None:
    """清理模块单例，避免跨测试污染。"""
    redis_mod._client = None
    redis_mod._fail_count = 0
    redis_mod._last_fail_ts = 0.0


class CountingLLM:
    """计数 LLM stub：记录 generate_json 调用次数。"""

    def __init__(self, sql: str = "SELECT SUM(sales) AS total FROM ds_demo LIMIT 1") -> None:
        self.calls = 0
        self._sql = sql

    async def generate_json(self, system: str, user: str) -> dict:
        self.calls += 1
        return {"sql": self._sql, "explanation": f"llm-gen-{self.calls}"}


async def _nl2sql_cache_aside(query: str, llm: CountingLLM) -> tuple[str, str, str]:
    """复刻 DataAgent 的 NL2SQL cache-aside（真实 get_json/set_json）。"""
    key = nl2sql_key("ds-1", text_hash("schema"), query)
    cached = await redis_mod.get_json(key)
    if cached and cached.get("sql"):
        return str(cached["sql"]), str(cached.get("explanation") or ""), "hit"
    result = await llm.generate_json("sys", "user")
    await redis_mod.set_json(
        key,
        {"sql": result["sql"], "explanation": result["explanation"]},
        TTL_SCHEMA,
    )
    return result["sql"], result["explanation"], "miss"


def test_cache_aside_set_then_get_roundtrip():
    """cache-aside 写入后可读回；TTL 按策略传递。"""
    fake = install_fake_redis()

    async def main():
        payload = {"sql": "SELECT 1", "explanation": "第一次生成"}
        key = nl2sql_key("ds-1", text_hash("schema"), "总销售额")
        ok = await redis_mod.set_json(key, payload, TTL_SCHEMA)
        assert ok is True
        assert fake.ttls[key] == TTL_SCHEMA
        got = await redis_mod.get_json(key)
        assert got == payload
        assert await redis_mod.get_json("cache:nl2sql:missing") is None

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_get_json_rejects_non_dict_payload():
    """坏数据（非 dict）按 MISS 处理，不抛异常。"""
    fake = install_fake_redis()
    fake.store["cache:bad"] = json.dumps(["not", "a", "dict"])

    async def main():
        assert await redis_mod.get_json("cache:bad") is None

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_cache_hit_skips_llm_call():
    """预热缓存后走 cache-aside：命中直接返回，LLM 调用次数为 0。"""
    fake = install_fake_redis()
    llm = CountingLLM()
    query = "总销售额是多少"
    seed_key = nl2sql_key("ds-1", text_hash("schema"), query)
    fake.store[seed_key] = json.dumps(
        {"sql": "SELECT SUM(sales) FROM ds_demo LIMIT 1", "explanation": "缓存命中"},
        ensure_ascii=False,
    )

    async def main():
        sql, explanation, state = await _nl2sql_cache_aside(query, llm)
        assert state == "hit"
        assert sql == "SELECT SUM(sales) FROM ds_demo LIMIT 1"
        assert explanation == "缓存命中"
        assert llm.calls == 0
        assert fake.get_calls == 1
        assert fake.set_calls == 0

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_cache_miss_calls_llm_then_stores():
    """空缓存：调用 LLM 一次并回写；再次调用变为 hit 且 LLM 不再触发。"""
    fake = install_fake_redis()
    llm = CountingLLM(
        sql="SELECT region, SUM(sales) FROM ds_demo GROUP BY region LIMIT 1000"
    )
    query = "按地区统计销售额"

    async def main():
        sql, _explanation, state = await _nl2sql_cache_aside(query, llm)
        assert state == "miss"
        assert llm.calls == 1
        assert "GROUP BY region" in sql
        assert fake.set_calls == 1

        key = nl2sql_key("ds-1", text_hash("schema"), query)
        assert key in fake.store
        stored = json.loads(fake.store[key])
        assert stored["sql"] == sql
        assert fake.ttls[key] == TTL_SCHEMA

        sql2, _e2, state2 = await _nl2sql_cache_aside(query, llm)
        assert state2 == "hit"
        assert sql2 == sql
        assert llm.calls == 1

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_cache_failure_degrades_gracefully():
    """Redis 读写抛异常：get_json→None、set_json→False、delete→False，不抛出。"""
    fake = install_fake_redis()
    fake.fail_get = True
    fake.fail_set = True
    fake.fail_delete = True

    async def main():
        assert await redis_mod.get_json("cache:nl2sql:x") is None
        assert await redis_mod.set_json("cache:nl2sql:x", {"sql": "SELECT 1"}, 60) is False
        assert await redis_mod.delete_key("cache:nl2sql:x") is False

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_cache_failure_still_completes_nl2sql_flow():
    """缓存故障时 cache-aside 流程仍走通：LLM 被调用，结果正常返回。"""
    fake = install_fake_redis()
    fake.fail_get = True
    fake.fail_set = True
    llm = CountingLLM()
    query = "总销售额"

    async def main():
        sql, _e, state = await _nl2sql_cache_aside(query, llm)
        assert state == "miss"
        assert llm.calls == 1
        assert sql  # 结果仍可用

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_get_redis_none_in_cooldown_skips_io():
    """连接失败进入冷却期：get_redis 直接 None，上层全按 MISS/降级。"""
    reset_redis_module()

    async def main():
        redis_mod._fail_count = 1
        redis_mod._last_fail_ts = time.monotonic()
        assert await redis_mod.get_redis() is None
        assert await redis_mod.get_json("k") is None
        assert await redis_mod.set_json("k", {"a": 1}, 10) is False
        ok, existing = await redis_mod.acquire_lock("lock:k", "v")
        assert ok is True and existing is None
        assert await redis_mod.release_lock("lock:k") is False

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()


def test_acquire_lock_and_release_with_fake_redis():
    """幂等锁：首次 SET NX 成功，重复提交返回已有 value，release 删除。"""
    fake = install_fake_redis()

    async def main():
        ok, existing = await redis_mod.acquire_lock("agent:lock:a", "task-1", ttl=60)
        assert ok is True and existing is None
        ok2, existing2 = await redis_mod.acquire_lock("agent:lock:a", "task-2", ttl=60)
        assert ok2 is False and existing2 == "task-1"
        assert await redis_mod.release_lock("agent:lock:a") is True
        ok3, _ = await redis_mod.acquire_lock("agent:lock:a", "task-3", ttl=60)
        assert ok3 is True

    try:
        asyncio.run(main())
    finally:
        reset_redis_module()
