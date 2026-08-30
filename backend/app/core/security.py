"""密码哈希与 JWT 令牌（CONTRACTS3 §3.2）。

- hash_password：PBKDF2-HMAC-SHA256，100k 迭代，16 字节随机盐，存储格式 `salt_hex$hash_hex`。
- verify_password：恒定时间比较（hmac.compare_digest），格式非法一律 False。
- create_token / decode_token：PyJWT HS256，secret 复用 crypto 的 APP_SECRET 链路
  （settings.APP_SECRET → .env 兜底 → 首次使用自动生成并写回 .env，与 Fernet 同源）。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 16
_TOKEN_TTL_S = 7 * 24 * 3600  # exp 7d


# ---------- APP_SECRET（与 crypto 同链路） ----------

def get_app_secret() -> str:
    """取 JWT 签名密钥：优先 settings.APP_SECRET；为空则借 crypto 链路
    （自动生成 Fernet key 并写回 .env，幂等）后从 .env 读回。"""
    secret = (getattr(settings, "APP_SECRET", "") or "").strip()
    if secret:
        return secret
    try:
        from app.core.crypto import _get_fernet, _read_env_app_secret

        _get_fernet()  # 无 APP_SECRET 时自动生成并追加写回 .env（幂等）
        secret = _read_env_app_secret().strip()
    except Exception as exc:  # pragma: no cover - crypto 故障不应阻断鉴权
        logger.warning("读取 APP_SECRET 失败，使用进程内临时密钥: %s", exc)
    return secret or secrets.token_hex(32)


# ---------- 密码（单向 PBKDF2，不用 Fernet） ----------

def hash_password(password: str) -> str:
    """明文密码 -> `salt_hex$hash_hex`（PBKDF2-HMAC-SHA256, 100k 迭代）。"""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码；stored 格式非法/参数异常一律 False（不抛出）。"""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ---------- JWT（HS256，exp 7d） ----------

def create_token(user_id: str, username: str) -> str:
    """签发 HS256 token：sub=user_id, username, iat, exp=iat+7d。"""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + _TOKEN_TTL_S,
    }
    return jwt.encode(payload, get_app_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    """解析并校验 token，返回 {"user_id", "username"}；过期/篡改/格式错误抛 ValueError。"""
    try:
        payload = jwt.decode(token, get_app_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError(f"token 无效或已过期: {exc}") from exc
    user_id = payload.get("sub")
    username = payload.get("username")
    if not user_id or not isinstance(user_id, str):
        raise ValueError("token 缺少用户标识")
    return {"user_id": user_id, "username": str(username or "")}
