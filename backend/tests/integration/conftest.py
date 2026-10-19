"""集成测试公共夹具：TestClient + 已登录请求头。

lifespan 轻量化：跳过 MySQL 迁移与 Redis 残留锁清扫，
使集成测试保持离线（不依赖 MySQL / Redis / Docker）。
各测试文件可用同名 fixture 覆盖以追加自己的 mock。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token

# 公共测试用户：各 API 测试的 mock 数据用同一 user_id，便于断言数据隔离
TEST_USER_ID = "user-v3"
TEST_USERNAME = "v3-user"


@pytest.fixture
def auth_headers() -> dict:
    """已登录请求头（Bearer token，7 天有效）。"""
    token = create_token(TEST_USER_ID, TEST_USERNAME)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    """已启动的 TestClient，lifespan 中的外部依赖全部置空。"""
    monkeypatch.setattr("app.persistence.migrations.run_migrations", lambda: [])

    async def _noop(*_a, **_k):
        return 0

    monkeypatch.setattr("app.cache.redis.cleanup_stale_locks", _noop)
    monkeypatch.setattr("app.cache.redis.close_redis", _noop)

    from app.main import app

    with TestClient(app) as c:
        yield c
