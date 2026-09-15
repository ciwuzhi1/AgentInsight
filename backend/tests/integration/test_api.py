"""API 集成测试：POST/GET /api/tasks、SSE 事件流、401 鉴权。

用 TestClient + monkeypatch 隔离 MySQL / Redis / Agent 执行；
后台 _run 替换为立即发布事件序列的 fake，保证 GET/SSE 可确定断言。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token


@pytest.fixture
def auth_headers() -> dict:
    token = create_token("user-itest", "itest-user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    # ---- lifespan 轻量化：跳过迁移与 Redis 清扫 ----
    monkeypatch.setattr("app.persistence.migrations.run_migrations", lambda: [])

    async def _noop(*_a, **_k):
        return 0

    monkeypatch.setattr("app.cache.redis.cleanup_stale_locks", _noop)
    monkeypatch.setattr("app.cache.redis.close_redis", _noop)

    # ---- MySQL mock ----
    def fake_get_dataset(ds_id: str):
        return {
            "id": ds_id,
            "name": "demo",
            "path": "/tmp/demo.csv",
            "schema_json": [{"name": "region", "type": "VARCHAR"}],
            "user_id": None,
        }

    monkeypatch.setattr("app.api.agent.get_dataset", fake_get_dataset)
    monkeypatch.setattr("app.api.agent.insert_task", lambda *_a, **_k: None)

    async def _no_persist(_state, _event):
        pass

    monkeypatch.setattr("app.api.agent._persist_event", _no_persist)
    monkeypatch.setattr("app.api.agent._register_agents", lambda: None)

    # ---- Redis 幂等锁 mock ----
    async def _acq(_key, _value, _ttl):
        return True, None

    async def _rel(_key):
        return True

    monkeypatch.setattr("app.api.agent.acquire_lock", _acq)
    monkeypatch.setattr("app.api.agent.release_lock", _rel)

    # ---- 后台任务：立即发布完整事件序列 ----
    from app.api.agent import bus

    async def fake_run(state):
        await bus.publish(
            state.task_id,
            {"type": "state", "status": "routing", "task_id": state.task_id},
        )
        await bus.publish(
            state.task_id,
            {
                "type": "plan",
                "steps": [{"id": "data", "agent": "data_agent", "depends_on": []}],
            },
        )
        await bus.publish(
            state.task_id,
            {"type": "agent_start", "agent": "data_agent", "step": "data"},
        )
        await bus.publish(
            state.task_id,
            {
                "type": "agent_end",
                "agent": "data_agent",
                "step": "data",
                "latency_ms": 3,
                "status": "ok",
                "detail": {"message_type": "data_result"},
            },
        )
        final = {
            "task_id": state.task_id,
            "query": state.query,
            "engine": "duckdb",
            "sql": "SELECT 1",
            "explanation": "集成测试",
            "columns": ["x"],
            "rows": [[1]],
            "row_count": 1,
            "truncated": False,
            "chart": None,
        }
        await bus.publish(state.task_id, {"type": "final", "result": final})

    monkeypatch.setattr("app.api.agent._run", fake_run)

    from app.main import app

    with TestClient(app) as c:
        yield c


def _create_task(client: TestClient, headers: dict, query: str = "总销售额") -> str:
    resp = client.post(
        "/api/tasks",
        json={"dataset_id": "demo-ds", "query": query},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def _wait_completed(client: TestClient, task_id: str, headers: dict, timeout: float = 3.0) -> dict:
    """轮询 GET /api/tasks/{id} 直到 completed（TestClient portal 线程跑后台任务）。"""
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("status") == "completed":
            return body
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内完成: {body}")


def test_post_task_creates_task(client, auth_headers):
    """POST /api/tasks 返回 200 + task_id + status=created。"""
    resp = client.post(
        "/api/tasks",
        json={"dataset_id": "demo-ds", "query": "总销售额"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["task_id"]


def test_post_task_missing_dataset_404(client, auth_headers, monkeypatch):
    """数据集不存在 → 404。"""

    def _none(_ds_id):
        return None

    monkeypatch.setattr("app.api.agent.get_dataset", _none)
    resp = client.post(
        "/api/tasks",
        json={"dataset_id": "nope", "query": "q"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_get_task_returns_task(client, auth_headers):
    """GET /api/tasks/{id} 从 Bus 回放：status/engine/final_result/steps。"""
    task_id = _create_task(client, auth_headers)
    body = _wait_completed(client, task_id, auth_headers)

    assert body["task_id"] == task_id
    assert body["status"] == "completed"
    assert body["engine"] == "duckdb"
    assert body["final_result"]["columns"] == ["x"]
    assert body["final_result"]["row_count"] == 1
    assert any(s["agent_name"] == "data_agent" and s["status"] == "ok" for s in body["steps"])


def test_get_unknown_task_404(client, auth_headers):
    resp = client.get("/api/tasks/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404


def test_sse_event_stream_format(client, auth_headers):
    """SSE：Content-Type text/event-stream，data: {json} 帧 + event: done 收尾。"""
    task_id = _create_task(client, auth_headers)
    _wait_completed(client, task_id, auth_headers)

    with client.stream(
        "GET", f"/api/tasks/{task_id}/events", headers=auth_headers
    ) as sse:
        assert sse.status_code == 200
        assert sse.headers["content-type"].startswith("text/event-stream")
        raw = "".join(sse.iter_text())

    assert "data: " in raw
    assert "event: done" in raw

    types: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[len("data: "):])
        assert isinstance(payload, dict)
        # 心跳消息 {} 没有 type 字段，跳过
        if not payload:
            continue
        assert "type" in payload
        types.append(payload["type"])

    assert "agent_start" in types
    assert "agent_end" in types
    assert "final" in types


def test_sse_query_token_accepted(client, auth_headers):
    """SSE 允许 ?token= 查询参数兜底（EventSource 无法带 header）。"""
    task_id = _create_task(client, auth_headers)
    _wait_completed(client, task_id, auth_headers)

    token = auth_headers["Authorization"].split(" ", 1)[1]
    resp = client.get(f"/api/tasks/{task_id}/events?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_401_without_auth_token(client):
    """无 Authorization：POST/GET/SSE 一律 401。"""
    resp = client.post("/api/tasks", json={"dataset_id": "x", "query": "q"})
    assert resp.status_code == 401

    resp = client.get("/api/tasks/any-id")
    assert resp.status_code == 401

    resp = client.get("/api/tasks/any-id/events")
    assert resp.status_code == 401


def test_401_with_invalid_token(client):
    """伪造 token 一律 401。"""
    headers = {"Authorization": "Bearer not-a-valid-jwt"}
    resp = client.get("/api/tasks/any-id", headers=headers)
    assert resp.status_code == 401
    resp = client.post(
        "/api/tasks", json={"dataset_id": "x", "query": "q"}, headers=headers
    )
    assert resp.status_code == 401
