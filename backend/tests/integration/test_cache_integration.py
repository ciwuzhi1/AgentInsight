"""Cache 集成测试：cache-aside 模式端到端、命中/未命中 LLM 计数、故障降级。

与 test_cache.py 互补：本文件聚焦 cache-aside 模式的完整生命周期、
并发场景、TTL 策略正确性。
"""
from __future__ import annotations

import asyncio
import json
import time

from app.cache import redis as redis_mod
from app.cache.keys import bytes_hash, nl2sql_key, resume_key, text_hash
from app.cache.policies import TTL_JOB, TTL_RESUME, TTL_SCHEMA, get_ttl


class FakeRedis:
    """内存版 redis.asyncio 子集。"""

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
    fake = fake or FakeRedis()
    redis_mod._client = fake
    redis_mod._fail_count = 0
    redis_mod._last_fail_ts = 0.0
    return fake


def reset_redis_module() -> None:
    redis_mod._client = None
    redis_mod._fail_count = 0
    redis_mod._last_fail_ts = 0.0


class CountingLLM:
    """计数 LLM stub。"""

    def __init__(self, responses: list[dict] | None = None) -> None:
        self.calls = 0
        self._responses = responses or [
            {"sql": "SELECT 1", "explanation": "default"}
        ]

    async def generate_json(self, system: str, user: str) -> dict:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return dict(self._responses[idx])


async def _cache_aside_nl2sql(
    dataset_id: str, schema_hash: str, query: str, llm: CountingLLM
) -> tuple[dict, str]:
    """复刻 DataAgent 的 NL2SQL cache-aside。"""
    key = nl2sql_key(dataset_id, schema_hash, query)
    cached = await redis_mod.get_json(key)
    if cached and cached.get("sql"):
        return cached, "hit"
    result = await llm.generate_json("sys", "user")
    await redis_mod.set_json(
        key, {"sql": result["sql"], "explanation": result["explanation"]}, TTL_SCHEMA
    )
    return result, "miss"


async def _cache_aside_resume(
    content_hash: str, profile: dict, llm: CountingLLM
) -> tuple[dict, str]:
    """复刻 resume_agent 的 cache-aside。"""
    key = resume_key(content_hash)
    cached = await redis_mod.get_json(key)
    if cached and cached.get("skills"):
        return cached, "hit"
    result = await llm.generate_json("sys", "user")
    await redis_mod.set_json(key, result, TTL_RESUME)
    return result, "miss"


# ---------- Cache-aside 模式端到端 ----------


class TestCacheAsideEndToEnd:
    def test_full_lifecycle_miss_then_hit(self):
        """完整生命周期：空缓存 miss → 回写 → 再次请求 hit。"""
        fake = install_fake_redis()
        llm = CountingLLM([{"sql": "SELECT SUM(sales) FROM t", "explanation": "第一次"}])
        query = "总销售额"
        ds_id = "ds-1"
        schema_h = text_hash("schema")

        try:
            result1, state1 = asyncio.run(
                _cache_aside_nl2sql(ds_id, schema_h, query, llm)
            )
            assert state1 == "miss"
            assert llm.calls == 1
            assert result1["sql"] == "SELECT SUM(sales) FROM t"

            # 验证缓存已写入
            key = nl2sql_key(ds_id, schema_h, query)
            assert key in fake.store
            assert fake.ttls[key] == TTL_SCHEMA

            result2, state2 = asyncio.run(
                _cache_aside_nl2sql(ds_id, schema_h, query, llm)
            )
            assert state2 == "hit"
            assert llm.calls == 1  # 未再次调用 LLM
            assert result2["sql"] == result1["sql"]
        finally:
            reset_redis_module()

    def test_different_queries_are_independent(self):
        """不同查询产生不同缓存 key，互不干扰。"""
        fake = install_fake_redis()
        llm = CountingLLM(
            [
                {"sql": "SELECT 1", "explanation": "q1"},
                {"sql": "SELECT 2", "explanation": "q2"},
            ]
        )
        ds_id = "ds-1"
        schema_h = text_hash("schema")

        try:
            r1, s1 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "问题A", llm))
            r2, s2 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "问题B", llm))
            assert s1 == "miss" and s2 == "miss"
            assert llm.calls == 2
            assert r1["sql"] != r2["sql"]
            assert len(fake.store) == 2
        finally:
            reset_redis_module()

    def test_different_datasets_use_different_keys(self):
        """不同数据集的相同查询使用不同缓存 key。"""
        fake = install_fake_redis()
        llm = CountingLLM()
        schema_h = text_hash("schema")

        try:
            asyncio.run(_cache_aside_nl2sql("ds-1", schema_h, "问题", llm))
            asyncio.run(_cache_aside_nl2sql("ds-2", schema_h, "问题", llm))
            assert llm.calls == 2  # 两次 miss
            assert len(fake.store) == 2
        finally:
            reset_redis_module()

    def test_resume_cache_aside_lifecycle(self):
        """简历缓存 cache-aside：miss → 回写 → hit，TTL 使用 TTL_RESUME。"""
        fake = install_fake_redis()
        llm = CountingLLM(
            [{"skills": ["Python", "SQL"], "education": "本科", "experience_years": 3}]
        )
        content_h = bytes_hash(b"resume-pdf-bytes")

        try:
            profile, state1 = asyncio.run(_cache_aside_resume(content_h, {}, llm))
            assert state1 == "miss"
            assert llm.calls == 1

            key = resume_key(content_h)
            assert key in fake.store
            assert fake.ttls[key] == TTL_RESUME

            profile2, state2 = asyncio.run(_cache_aside_resume(content_h, {}, llm))
            assert state2 == "hit"
            assert llm.calls == 1
            assert profile2["skills"] == ["Python", "SQL"]
        finally:
            reset_redis_module()


# ---------- 命中 vs 未命中 LLM 调用计数 ----------


class TestLLMCallCounts:
    def test_n_misses_then_hits(self):
        """N 次不同查询全部 miss（N 次 LLM），再全部 hit（0 次 LLM）。"""
        fake = install_fake_redis()
        queries = [f"问题{i}" for i in range(5)]
        llm = CountingLLM(
            [{"sql": f"SELECT {i}", "explanation": f"e{i}"} for i in range(5)]
        )
        ds_id = "ds-1"
        schema_h = text_hash("schema")

        try:
            # 第一轮：全部 miss
            for q in queries:
                _, state = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, q, llm))
                assert state == "miss"
            assert llm.calls == 5

            # 第二轮：全部 hit
            for q in queries:
                _, state = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, q, llm))
                assert state == "hit"
            assert llm.calls == 5  # 无新增调用
        finally:
            reset_redis_module()

    def test_interleaved_miss_and_hit(self):
        """交替执行：新查询 miss，旧查询 hit，LLM 调用次数正确。"""
        fake = install_fake_redis()
        llm = CountingLLM(
            [{"sql": f"SELECT {i}", "explanation": f"e{i}"} for i in range(10)]
        )
        ds_id = "ds-1"
        schema_h = text_hash("schema")

        try:
            # 第 1 次：miss
            _, s1 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "q1", llm))
            assert s1 == "miss" and llm.calls == 1

            # 第 2 次：hit（同 key）
            _, s2 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "q1", llm))
            assert s2 == "hit" and llm.calls == 1

            # 第 3 次：miss（新 key）
            _, s3 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "q2", llm))
            assert s3 == "miss" and llm.calls == 2

            # 第 4 次：hit（q1）
            _, s4 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "q1", llm))
            assert s4 == "hit" and llm.calls == 2

            # 第 5 次：hit（q2）
            _, s5 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, "q2", llm))
            assert s5 == "hit" and llm.calls == 2
        finally:
            reset_redis_module()

    def test_stale_cache_after_ttl_expiry(self):
        """模拟 TTL 过期（手动删除 key）后重新 miss。"""
        fake = install_fake_redis()
        llm = CountingLLM(
            [
                {"sql": "SELECT v1", "explanation": "first"},
                {"sql": "SELECT v2", "explanation": "second"},
            ]
        )
        ds_id = "ds-1"
        schema_h = text_hash("schema")
        query = "查询"

        try:
            r1, s1 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, query, llm))
            assert s1 == "miss" and r1["sql"] == "SELECT v1"

            # 模拟 TTL 过期
            key = nl2sql_key(ds_id, schema_h, query)
            del fake.store[key]

            r2, s2 = asyncio.run(_cache_aside_nl2sql(ds_id, schema_h, query, llm))
            assert s2 == "miss" and llm.calls == 2
            assert r2["sql"] == "SELECT v2"
        finally:
            reset_redis_module()


# ---------- 缓存故障降级 ----------


class TestCacheFailureDegradation:
    def test_get_failure_degrades_to_miss(self):
        """get 失败：按 MISS 处理，LLM 仍被调用。"""
        fake = install_fake_redis()
        fake.fail_get = True
        llm = CountingLLM()

        try:
            result, state = asyncio.run(
                _cache_aside_nl2sql("ds-1", text_hash("s"), "q", llm)
            )
            assert state == "miss"
            assert llm.calls == 1
            assert result["sql"]  # 结果可用
        finally:
            reset_redis_module()

    def test_set_failure_still_returns_result(self):
        """set 失败：结果仍返回，只是未缓存。"""
        fake = install_fake_redis()
        fake.fail_set = True
        llm = CountingLLM()

        try:
            result, state = asyncio.run(
                _cache_aside_nl2sql("ds-1", text_hash("s"), "q", llm)
            )
            assert state == "miss"
            assert llm.calls == 1
            assert result["sql"]

            # 未写入缓存
            assert len(fake.store) == 0
        finally:
            reset_redis_module()

    def test_full_redis_failure_still_completes(self):
        """get+set 全部失败：流程仍走通，LLM 被调用，结果正常。"""
        fake = install_fake_redis()
        fake.fail_get = True
        fake.fail_set = True
        llm = CountingLLM([{"sql": "SELECT fallback", "explanation": "降级"}])

        try:
            result, state = asyncio.run(
                _cache_aside_nl2sql("ds-1", text_hash("s"), "q", llm)
            )
            assert state == "miss"
            assert llm.calls == 1
            assert result["sql"] == "SELECT fallback"
        finally:
            reset_redis_module()

    def test_cooldown_prevents_repeated_connection_attempts(self):
        """连接失败进入冷却期：后续请求直接返回 None，不再尝试连接。"""
        reset_redis_module()

        try:
            # 模拟失败进入冷却
            redis_mod._fail_count = 1
            redis_mod._last_fail_ts = time.monotonic()

            async def main():
                assert await redis_mod.get_redis() is None
                assert await redis_mod.get_json("any-key") is None
                assert await redis_mod.set_json("any-key", {"a": 1}, 10) is False

            asyncio.run(main())
        finally:
            reset_redis_module()

    def test_cooldown_expiry_allows_retry(self):
        """冷却期过后允许重试连接。"""
        fake = install_fake_redis()
        redis_mod._fail_count = 1
        redis_mod._last_fail_ts = time.monotonic() - 10.0  # 冷却已过（5s）

        try:
            async def main():
                # 冷却已过，FakeRedis 已注入，应返回 client
                client = await redis_mod.get_redis()
                assert client is not None

            asyncio.run(main())
        finally:
            reset_redis_module()

    def test_resume_cache_failure_degrades(self):
        """简历缓存故障：cache-aside 流程仍走通。"""
        fake = install_fake_redis()
        fake.fail_get = True
        fake.fail_set = True
        llm = CountingLLM([{"skills": ["Go"], "education": "本科"}])

        try:
            profile, state = asyncio.run(
                _cache_aside_resume(bytes_hash(b"data"), {}, llm)
            )
            assert state == "miss"
            assert llm.calls == 1
            assert profile["skills"] == ["Go"]
        finally:
            reset_redis_module()


# ---------- TTL 策略正确性 ----------


class TestTTLStrategies:
    def test_resume_uses_24h_ttl(self):
        """简历缓存 TTL = 24 小时。"""
        fake = install_fake_redis()

        try:
            async def main():
                await redis_mod.set_json("cache:resume:x", {"a": 1}, TTL_RESUME)

            asyncio.run(main())
            assert fake.ttls["cache:resume:x"] == 24 * 3600
        finally:
            reset_redis_module()

    def test_job_uses_24h_ttl(self):
        """岗位缓存 TTL = 24 小时。"""
        fake = install_fake_redis()

        try:
            async def main():
                await redis_mod.set_json("cache:job:x", {"a": 1}, TTL_JOB)

            asyncio.run(main())
            assert fake.ttls["cache:job:x"] == 24 * 3600
        finally:
            reset_redis_module()

    def test_schema_uses_1h_ttl(self):
        """NL2SQL 缓存 TTL = 1 小时。"""
        fake = install_fake_redis()

        try:
            async def main():
                await redis_mod.set_json("cache:nl2sql:x", {"a": 1}, TTL_SCHEMA)

            asyncio.run(main())
            assert fake.ttls["cache:nl2sql:x"] == 3600
        finally:
            reset_redis_module()

    def test_get_ttl_mapping(self):
        """get_ttl 按类别返回正确 TTL。"""
        assert get_ttl("resume") == TTL_RESUME
        assert get_ttl("job") == TTL_JOB
        assert get_ttl("schema") == TTL_SCHEMA
        assert get_ttl("unknown") == TTL_SCHEMA  # 默认回退


# ---------- 幂等锁集成 ----------


class TestIdempotencyLock:
    def test_lock_prevents_duplicate_execution(self):
        """幂等锁：首次获得，重复提交被拒绝。"""
        fake = install_fake_redis()

        try:
            async def main():
                ok1, existing1 = await redis_mod.acquire_lock(
                    "agent:lock:task-1", "worker-a", ttl=60
                )
                assert ok1 is True and existing1 is None

                ok2, existing2 = await redis_mod.acquire_lock(
                    "agent:lock:task-1", "worker-b", ttl=60
                )
                assert ok2 is False and existing2 == "worker-a"

                # 释放后可重新获得
                assert await redis_mod.release_lock("agent:lock:task-1") is True
                ok3, _ = await redis_mod.acquire_lock(
                    "agent:lock:task-1", "worker-c", ttl=60
                )
                assert ok3 is True

            asyncio.run(main())
        finally:
            reset_redis_module()

    def test_lock_degrades_when_redis_down(self):
        """Redis 不可用时锁降级放行（返回 True, None）。"""
        reset_redis_module()
        redis_mod._fail_count = 1
        redis_mod._last_fail_ts = time.monotonic()

        try:
            async def main():
                ok, existing = await redis_mod.acquire_lock("lock:x", "v", ttl=60)
                assert ok is True and existing is None

            asyncio.run(main())
        finally:
            reset_redis_module()

    def test_get_json_rejects_non_dict(self):
        """非 dict 缓存数据按 MISS 处理。"""
        fake = install_fake_redis()
        fake.store["cache:bad"] = json.dumps(["not", "a", "dict"])

        try:
            async def main():
                assert await redis_mod.get_json("cache:bad") is None

            asyncio.run(main())
        finally:
            reset_redis_module()

    def test_get_json_rejects_invalid_json(self):
        """非法 JSON 按 MISS 处理，不抛异常。"""
        fake = install_fake_redis()
        fake.store["cache:broken"] = "{not valid json"

        try:
            async def main():
                assert await redis_mod.get_json("cache:broken") is None

            asyncio.run(main())
        finally:
            reset_redis_module()
