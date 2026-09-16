"""V3.0 增补接口集成测试：DELETE /api/datasets、GET /api/jobs、GET /api/health/duckdb。

同时回归校验既有 GET /api/tasks/{id}/export 与 GET /api/resumes/{id}。
用 TestClient + monkeypatch 隔离 MySQL / DuckDB / 文件系统。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token


@pytest.fixture
def auth_headers() -> dict:
    token = create_token("user-v3", "v3-user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.persistence.migrations.run_migrations", lambda: [])

    async def _noop(*_a, **_k):
        return 0

    monkeypatch.setattr("app.cache.redis.cleanup_stale_locks", _noop)
    monkeypatch.setattr("app.cache.redis.close_redis", _noop)

    from app.main import app

    with TestClient(app) as c:
        yield c


# ---------- DELETE /api/datasets/{id} ----------


def test_delete_dataset_success(client, auth_headers, monkeypatch, tmp_path):
    """本人数据集：注销视图 → 删文件 → 删登记 → 200。"""
    csv_path = tmp_path / "ds_del.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.api.datasets.get_dataset",
        lambda ds_id: {
            "id": ds_id,
            "name": "del",
            "path": str(csv_path),
            "user_id": "user-v3",
        },
    )
    unregistered: list[str] = []
    import app.data_engine.duckdb_engine as duck_mod

    monkeypatch.setattr(
        duck_mod.duckdb_engine, "unregister_dataset", lambda ds_id: unregistered.append(ds_id)
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        "app.api.datasets.delete_dataset", lambda ds_id: deleted.append(ds_id)
    )

    resp = client.delete("/api/datasets/abc12345", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dataset_id"] == "abc12345"
    assert body["deleted"] is True
    assert unregistered == ["abc12345"]
    assert deleted == ["abc12345"]
    assert not csv_path.exists()


def test_delete_dataset_not_found(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.datasets.get_dataset", lambda _id: None)
    resp = client.delete("/api/datasets/nope", headers=auth_headers)
    assert resp.status_code == 404


def test_delete_dataset_other_user_404(client, auth_headers, monkeypatch):
    """他人数据集不可见 → 404（不泄露存在性）。"""
    monkeypatch.setattr(
        "app.api.datasets.get_dataset",
        lambda ds_id: {"id": ds_id, "path": None, "user_id": "someone-else"},
    )
    resp = client.delete("/api/datasets/abc", headers=auth_headers)
    assert resp.status_code == 404


def test_delete_dataset_requires_auth(client):
    resp = client.delete("/api/datasets/abc")
    assert resp.status_code == 401


def test_delete_dataset_public_legacy_allowed(client, auth_headers, monkeypatch):
    """user_id=NULL 公共遗留：登录用户可删。"""
    monkeypatch.setattr(
        "app.api.datasets.get_dataset",
        lambda ds_id: {"id": ds_id, "path": None, "user_id": None},
    )
    import app.data_engine.duckdb_engine as duck_mod

    monkeypatch.setattr(duck_mod.duckdb_engine, "unregister_dataset", lambda _id: None)
    monkeypatch.setattr("app.api.datasets.delete_dataset", lambda _id: None)
    resp = client.delete("/api/datasets/legacy", headers=auth_headers)
    assert resp.status_code == 200


# ---------- GET /api/jobs ----------


def test_list_jobs_pagination(client, monkeypatch):
    fake_rows = [
        {"id": 3, "title": "A", "company": "C1", "location": "L1", "skills": "py"},
        {"id": 2, "title": "B", "company": "C2", "location": "L2", "skills": "go"},
    ]
    captured: dict = {}

    def fake_list_jobs(limit=20, offset=0):
        captured["limit"] = limit
        captured["offset"] = offset
        return fake_rows

    monkeypatch.setattr("app.api.crawler.list_jobs", fake_list_jobs)
    monkeypatch.setattr("app.api.crawler.count_jobs", lambda: 2)

    resp = client.get("/api/jobs?page=1&limit=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page"] == 1
    assert body["limit"] == 2
    assert body["count"] == 2
    assert body["total"] == 2
    assert body["has_more"] is False
    assert body["items"][0]["title"] == "A"
    assert captured == {"limit": 2, "offset": 0}


def test_list_jobs_page2_offset(client, monkeypatch):
    monkeypatch.setattr("app.api.crawler.list_jobs", lambda limit=20, offset=0: [])
    monkeypatch.setattr("app.api.crawler.count_jobs", lambda: 50)
    resp = client.get("/api/jobs?page=3&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 3
    assert body["has_more"] is True  # 0 items on page but total 50


def test_list_jobs_mysql_down(client, monkeypatch):
    from app.persistence.mysql import PersistenceError

    def _boom(*_a, **_k):
        raise PersistenceError("down")

    monkeypatch.setattr("app.api.crawler.list_jobs", _boom)
    resp = client.get("/api/jobs")
    assert resp.status_code == 503


# ---------- GET /api/health/duckdb ----------


def test_health_duckdb(client):
    resp = client.get("/api/health/duckdb")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duckdb"] == "up"
    assert isinstance(body["connections"], int)
    assert body["connections"] >= 0
    assert body["max_connections"] >= body["connections"]


def test_health_duckdb_reports_engine_connections(client):
    from app.data_engine.duckdb_engine import duckdb_engine

    # 注册一个假连接计数（不真正建连，直接塞 dict）
    duckdb_engine._conns["__probe__"] = object()  # type: ignore[assignment]
    try:
        resp = client.get("/api/health/duckdb")
        assert resp.status_code == 200
        assert resp.json()["connections"] >= 1
    finally:
        duckdb_engine._conns.pop("__probe__", None)
        duckdb_engine._tables.pop("__probe__", None)
        duckdb_engine._last_used.pop("__probe__", None)
        duckdb_engine._meta.pop("__probe__", None)


# ---------- 回归：既有 export / resume 详情 ----------


def test_task_export_csv_and_json(client, auth_headers, monkeypatch):
    """GET /api/tasks/{id}/export：csv 表格与 json 完整结果。"""
    final = {"columns": ["x", "y"], "rows": [[1, "a"], [2, "b"]], "engine": "duckdb"}
    monkeypatch.setattr(
        "app.api.agent.mysql_get_task_row",
        lambda task_id: {
            "id": task_id,
            "user_id": "user-v3",
            "final_result": final,
            "status": "completed",
        },
    )
    resp = client.get("/api/tasks/t1/export?format=csv", headers=auth_headers)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("x")  # utf-8-sig BOM then header
    assert "y" in lines[0]

    resp = client.get("/api/tasks/t1/export?format=json", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["columns"] == ["x", "y"]


def test_resume_detail_includes_profile(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        "app.api.resumes.get_resume",
        lambda rid: {
            "id": rid,
            "filename": "cv.pdf",
            "profile_json": {"name": "张三", "skills": ["Python"]},
            "user_id": "user-v3",
            "created_at": "2026-01-01 00:00:00",
        },
    )
    resp = client.get("/api/resumes/r1", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["resume_id"] == "r1"
    assert body["filename"] == "cv.pdf"
    assert body["profile"]["name"] == "张三"
    assert body["profile"]["skills"] == ["Python"]
