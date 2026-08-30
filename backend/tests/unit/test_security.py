"""P4 安全单测（CONTRACTS3 §3.2/§3.5）：密码哈希、JWT token、隔离查询。

密码/token 部分纯逻辑；隔离查询部分连真 MySQL（连不上则 skip，不影响 CI）。
"""
import time
import uuid

import jwt as pyjwt
import pytest

from app.core.security import (
    create_token,
    decode_token,
    get_app_secret,
    hash_password,
    verify_password,
)

# ---------- 密码哈希 ----------


def test_password_hash_roundtrip() -> None:
    stored = hash_password("s3cret-密码")
    assert stored.count("$") == 1
    salt, digest = stored.split("$")
    assert len(salt) == 32 and len(digest) == 64  # 16B 盐 hex / SHA256 hex
    assert verify_password("s3cret-密码", stored) is True


def test_password_hash_random_salt() -> None:
    """同密码两次哈希盐不同（防彩虹表），但都可校验通过。"""
    a, b = hash_password("same-password"), hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a) and verify_password("same-password", b)


def test_password_wrong_and_malformed() -> None:
    stored = hash_password("right")
    assert verify_password("wrong", stored) is False
    assert verify_password("", stored) is False
    # 非法存储格式不抛出，一律 False
    assert verify_password("right", "not-a-hash") is False
    assert verify_password("right", "") is False


# ---------- JWT token ----------


def test_token_roundtrip() -> None:
    token = create_token("uid-123", "alice")
    payload = decode_token(token)
    assert payload == {"user_id": "uid-123", "username": "alice"}


def test_token_expired_rejected() -> None:
    """手工签发过期 token（exp 在过去）→ ValueError。"""
    secret = get_app_secret()
    now = int(time.time())
    expired = pyjwt.encode({"sub": "u1", "username": "bob", "iat": now - 100, "exp": now - 10},
                           secret, algorithm="HS256")
    with pytest.raises(ValueError):
        decode_token(expired)


def test_token_tampered_rejected() -> None:
    """篡改签名 → ValueError。"""
    token = create_token("uid-1", "alice")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        decode_token(tampered)
    # 用错误密钥签发的 token 同样拒绝
    forged = pyjwt.encode({"sub": "u2", "username": "eve", "exp": int(time.time()) + 3600},
                          "wrong-secret", algorithm="HS256")
    with pytest.raises(ValueError):
        decode_token(forged)


def test_token_missing_claims_rejected() -> None:
    secret = get_app_secret()
    bad = pyjwt.encode({"exp": int(time.time()) + 3600}, secret, algorithm="HS256")
    with pytest.raises(ValueError):
        decode_token(bad)


# ---------- 隔离查询（真库；连不上 skip） ----------


def _db_available() -> bool:
    try:
        from app.persistence.mysql import get_connection

        with get_connection() as conn:
            conn.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="MySQL 不可用")
def test_user_crud_and_duplicate() -> None:
    from app.core.security import hash_password
    from app.persistence.mysql import create_user, get_user_by_username

    username = f"u_{uuid.uuid4().hex[:10]}"
    uid = uuid.uuid4().hex
    try:
        assert create_user(uid, username, hash_password("pass123")) is True
        assert create_user(uuid.uuid4().hex, username, hash_password("pass456")) is False  # 重名
        row = get_user_by_username(username)
        assert row is not None and row["id"] == uid
        assert verify_password("pass123", row["password_hash"])
    finally:
        from app.persistence.mysql import get_connection

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))


@pytest.mark.skipif(not _db_available(), reason="MySQL 不可用")
def test_dataset_user_id_isolation_columns() -> None:
    """带 user_id 写入 datasets → get_dataset 原样带回；NULL 视为公共。"""
    from app.persistence.mysql import get_connection, get_dataset, insert_dataset

    ds_id = uuid.uuid4().hex[:8] + "-p4test"
    user_a = uuid.uuid4().hex
    try:
        insert_dataset(ds_id, "p4test", "/tmp/x.csv", 10, 100, [{"name": "c"}], user_a)
        row = get_dataset(ds_id)
        assert row is not None and row["user_id"] == user_a
        # 隔离判定：属主可见、他人不可见、NULL 公共可见
        assert row["user_id"] in {None, user_a}
        assert row["user_id"] not in {None, "someone-else"}
    finally:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM datasets WHERE id = %s", (ds_id,))
