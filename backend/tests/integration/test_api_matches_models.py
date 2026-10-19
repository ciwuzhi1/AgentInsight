"""匹配任务与模型配置接口集成测试（此前零覆盖）。

覆盖：
- POST /api/matches：鉴权、参数校验、简历归属隔离、岗位存在性、幂等锁 409、成功创建。
- /api/models 全组：列表脱敏、新增（api_key 加密落库）、激活、删除、连通性测试。

全程离线：MySQL / Redis / 真实 LLM 均被 monkeypatch 隔离。
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.llm import LLMError
from tests.integration.conftest import TEST_USER_ID

# ---------- 公共夹具 ----------


@pytest.fixture(autouse=True)
def _fixed_fernet(monkeypatch):
    """固定 Fernet 密钥：避免测试向仓库 .env 写入 APP_SECRET（同一密钥全程复用）。"""
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr("app.core.crypto._get_fernet", lambda: fernet)


@pytest.fixture
def _resume(monkeypatch):
    """默认可用简历（归属当前测试用户）；返回记录调用的列表。"""

    def _install(row: dict | None = None):
        calls: list[str] = []
        monkeypatch.setattr(
            "app.api.matches.get_resume",
            lambda rid: (calls.append(rid), row)[1],
        )
        return calls

    return _install


# ---------- POST /api/matches ----------


def test_create_match_requires_auth(client):
    """未登录：401，不进入业务校验。"""
    resp = client.post("/api/matches", json={"resume_id": "r1", "job_ids": [1]})
    assert resp.status_code == 401


def test_create_match_rejects_empty_job_ids(client, auth_headers):
    """job_ids 为空：400。"""
    resp = client.post(
        "/api/matches", json={"resume_id": "r1", "job_ids": []}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert "job_ids" in resp.json()["detail"]


def test_create_match_resume_not_found(client, auth_headers, monkeypatch):
    """简历不存在：404。"""
    monkeypatch.setattr("app.api.matches.get_resume", lambda _rid: None)
    resp = client.post(
        "/api/matches", json={"resume_id": "ghost", "job_ids": [1]}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_create_match_hides_other_users_resume(client, auth_headers, monkeypatch):
    """数据隔离：他人简历一律 404（不泄露存在性）。"""
    monkeypatch.setattr(
        "app.api.matches.get_resume",
        lambda rid: {"id": rid, "filename": "cv.pdf", "path": "/tmp/cv.pdf", "user_id": "someone-else"},
    )
    resp = client.post(
        "/api/matches", json={"resume_id": "r-other", "job_ids": [1]}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_create_match_allows_legacy_public_resume(client, auth_headers, monkeypatch):
    """user_id 为 NULL 的历史公共简历仍可用（进入后续岗位校验）。"""
    monkeypatch.setattr(
        "app.api.matches.get_resume",
        lambda rid: {"id": rid, "filename": "cv.pdf", "path": "/tmp/cv.pdf", "user_id": None},
    )
    monkeypatch.setattr("app.api.matches.get_jobs_by_ids", lambda ids: [])
    resp = client.post(
        "/api/matches", json={"resume_id": "r-legacy", "job_ids": [1]}, headers=auth_headers
    )
    assert resp.status_code == 400  # 岗位查询为空，而非简历 404


def test_create_match_rejects_missing_jobs(client, auth_headers, monkeypatch):
    """部分岗位不存在：400（数量不匹配）。"""
    monkeypatch.setattr(
        "app.api.matches.get_resume",
        lambda rid: {"id": rid, "filename": "cv.pdf", "path": "/tmp/cv.pdf", "user_id": TEST_USER_ID},
    )
    monkeypatch.setattr(
        "app.api.matches.get_jobs_by_ids",
        lambda ids: [{"id": ids[0], "title": "后端", "company": "A", "location": "上海", "skills": "Python", "description": ""}],
    )
    resp = client.post(
        "/api/matches", json={"resume_id": "r1", "job_ids": [1, 2]}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert "岗位" in resp.json()["detail"]


def test_create_match_rate_limited(client, auth_headers, monkeypatch):
    """限流命中：429，且不查库。"""
    from app.api.matches import task_limiter

    monkeypatch.setattr(task_limiter, "allow", lambda _key: (False, 7.5))
    resp = client.post(
        "/api/matches", json={"resume_id": "r1", "job_ids": [1]}, headers=auth_headers
    )
    assert resp.status_code == 429
    assert "7.5" in resp.json()["detail"]


def test_create_match_conflict_on_existing_lock(client, auth_headers, monkeypatch):
    """幂等锁已被持有：409 + 既有 task_id（重复提交不重复执行）。"""
    monkeypatch.setattr(
        "app.api.matches.get_resume",
        lambda rid: {"id": rid, "filename": "cv.pdf", "path": "/tmp/cv.pdf", "user_id": TEST_USER_ID},
    )
    monkeypatch.setattr(
        "app.api.matches.get_jobs_by_ids",
        lambda ids: [{"id": i, "title": f"岗位{i}", "company": "A", "location": "上海", "skills": "Python", "description": ""} for i in ids],
    )

    async def _busy(_key, _value):
        return False, "existing-task-id"

    monkeypatch.setattr("app.api.matches.acquire_lock", _busy)
    resp = client.post(
        "/api/matches", json={"resume_id": "r1", "job_ids": [1]}, headers=auth_headers
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["task_id"] == "existing-task-id"
    assert "执行中" in body["detail"]


def test_create_match_success(client, auth_headers, monkeypatch):
    """成功创建：返回 task_id/created，落库、注册锁与归属、挂 Bus、启动执行。"""
    from app.api.matches import bus, task_limiter

    monkeypatch.setattr(task_limiter, "allow", lambda _key: (True, 0.0))
    monkeypatch.setattr(
        "app.api.matches.get_resume",
        lambda rid: {"id": rid, "filename": "cv.pdf", "path": "/tmp/cv.pdf", "user_id": TEST_USER_ID},
    )
    monkeypatch.setattr(
        "app.api.matches.get_jobs_by_ids",
        lambda ids: [
            {"id": i, "title": f"岗位{i}", "company": "A", "location": "上海", "skills": "Python", "description": ""}
            for i in ids
        ],
    )

    locks: list[tuple] = []
    owners: list[tuple] = []
    attached: list[str] = []
    inserted: list[tuple] = []
    started: list[str] = []

    async def _acq(key, value):
        locks.append((key, value))
        return True, None

    async def _run(_state):
        started.append(_state.task_id)

    monkeypatch.setattr("app.api.matches.acquire_lock", _acq)
    monkeypatch.setattr("app.api.matches.register_task_lock", lambda tid, key: locks.append(("registered", tid, key)))
    monkeypatch.setattr("app.api.matches.register_task_owner", lambda tid, uid: owners.append((tid, uid)))
    monkeypatch.setattr("app.api.matches.insert_task", lambda *a, **_k: inserted.append(a))
    monkeypatch.setattr("app.api.matches._run", _run)
    monkeypatch.setattr(bus, "attach", lambda tid, task: attached.append(tid))
    monkeypatch.setattr(bus, "start_sweeper", lambda: None)

    resp = client.post(
        "/api/matches", json={"resume_id": "r1", "job_ids": [7, 8, 7]}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]
    assert resp.json()["status"] == "created"

    # 岗位去重保序：3 个 id（含重复）→ 2 个
    assert inserted and inserted[0][3] == "created"
    assert inserted[0][2] == "简历与 2 个岗位匹配分析"
    assert inserted[0][4] == TEST_USER_ID
    assert owners == [(task_id, TEST_USER_ID)]
    assert attached == [task_id]
    assert started == [task_id]


# ---------- /api/models ----------


def _model_row(api_key_enc: str = "", **over: object) -> dict:
    row = {
        "id": "m1",
        "name": "gpt-4o-mini",
        "provider": "openai",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "is_active": 0,
        "api_key_enc": api_key_enc,
    }
    row.update(over)
    return row


def test_list_models_masks_api_key(client, auth_headers, monkeypatch):
    """列表：api_key 脱敏为 ***尾4位，绝不返回明文/密文。"""
    from app.core.crypto import encrypt_secret

    enc = encrypt_secret("sk-abcdefgh1234")
    monkeypatch.setattr(
        "app.api.models.list_model_configs",
        lambda: [_model_row(enc), _model_row("", id="m2"), _model_row("not-a-cipher", id="m3")],
    )
    resp = client.get("/api/models", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["api_key_masked"] == "***1234"
    assert enc not in resp.text  # 密文不外泄
    assert "sk-abcdefgh1234" not in resp.text  # 明文不外泄
    assert rows[1]["api_key_masked"] == ""  # 空 key
    assert rows[2]["api_key_masked"] == ""  # 解密失败 → 空，不抛异常
    assert rows[0]["is_active"] is False


def test_list_models_requires_auth(client):
    assert client.get("/api/models").status_code == 401


def test_create_model_encrypts_api_key(client, auth_headers, monkeypatch):
    """新增：api_key 以密文落库（可解回明文），默认不激活，响应不回显 key。"""
    from app.core.crypto import decrypt_secret

    saved: list[dict] = []
    monkeypatch.setattr("app.api.models.save_model_config", lambda cfg: saved.append(cfg) or cfg["id"])

    resp = client.post(
        "/api/models",
        json={
            "name": "本地模型",
            "provider": "openai",
            "base_url": "http://127.0.0.1:8000/v1",
            "api_key": "sk-secret-value",
            "model": "qwen",
            "temperature": 0.3,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "created"
    assert "sk-secret-value" not in resp.text

    assert len(saved) == 1
    cfg = saved[0]
    assert cfg["api_key_enc"] != "sk-secret-value"
    assert decrypt_secret(cfg["api_key_enc"]) == "sk-secret-value"
    assert cfg["is_active"] == 0
    assert cfg["temperature"] == 0.3


def test_create_model_rejects_empty_api_key(client, auth_headers, monkeypatch):
    """api_key 为空：422（Pydantic min_length）。"""
    monkeypatch.setattr("app.api.models.save_model_config", lambda cfg: cfg["id"])
    resp = client.post(
        "/api/models",
        json={"name": "x", "base_url": "http://x/v1", "api_key": "", "model": "m"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_activate_model(client, auth_headers, monkeypatch):
    """激活：存在则调用 activate，返回 is_active=True。"""
    activated: list[str] = []
    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [_model_row()])
    monkeypatch.setattr("app.api.models.activate_model_config", lambda mid: activated.append(mid))

    resp = client.put("/api/models/m1/activate", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"id": "m1", "is_active": True}
    assert activated == ["m1"]


def test_activate_model_not_found(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [])
    resp = client.put("/api/models/nope/activate", headers=auth_headers)
    assert resp.status_code == 404


def test_delete_model(client, auth_headers, monkeypatch):
    """删除：存在则删除，不存在 404。"""
    deleted: list[str] = []
    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [_model_row()])
    monkeypatch.setattr("app.api.models.delete_model_config", lambda mid: deleted.append(mid))

    resp = client.delete("/api/models/m1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"id": "m1", "deleted": True}
    assert deleted == ["m1"]

    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [])
    assert client.delete("/api/models/m1", headers=auth_headers).status_code == 404


def test_test_model_not_found(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [])
    assert client.post("/api/models/nope/test", headers=auth_headers).status_code == 404


def test_test_model_ok(client, auth_headers, monkeypatch):
    """连通性测试成功：ok=True + latency_ms。"""
    from app.core.crypto import encrypt_secret

    class _OkClient:
        def __init__(self, **_kw) -> None:
            pass

        async def generate_json(self, _system: str, _user: str) -> dict:
            return {"pong": True}

    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [_model_row(encrypt_secret("sk-ok"))])
    monkeypatch.setattr("app.api.models.OpenAICompatibleClient", _OkClient)

    resp = client.post("/api/models/m1/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["latency_ms"], int)


def test_test_model_reports_llm_error(client, auth_headers, monkeypatch):
    """连通性测试失败：ok=False + 错误原因，接口自身不 500。"""
    from app.core.crypto import encrypt_secret

    class _FailClient:
        def __init__(self, **_kw) -> None:
            pass

        async def generate_json(self, _system: str, _user: str) -> dict:
            raise LLMError("连接超时")

    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [_model_row(encrypt_secret("sk-bad"))])
    monkeypatch.setattr("app.api.models.OpenAICompatibleClient", _FailClient)

    resp = client.post("/api/models/m1/test", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "连接超时" in resp.json()["error"]


def test_test_model_handles_broken_ciphertext(client, auth_headers, monkeypatch):
    """密文损坏：直接返回解密失败，不构造 client、不 500。"""
    monkeypatch.setattr("app.api.models.list_model_configs", lambda: [_model_row("not-a-cipher")])
    resp = client.post("/api/models/m1/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "解密失败" in body["error"]
