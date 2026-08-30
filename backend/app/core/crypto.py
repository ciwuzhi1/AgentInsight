"""对称加密封装：Fernet 加解密模型 api_key 等敏感值（CONTRACTS2 §4.3）。

密钥来源 APP_SECRET：优先 config.settings；为空则从仓库根 .env 文本兜底解析
（config.py 不在 A4 允许改动清单内，故此处自行读 .env）；仍为空则自动生成
Fernet key 并追加写回 .env（幂等：已存在不重写）。
"""
from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# backend/app/core/crypto.py 上三级即仓库根
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _REPO_ROOT / ".env"


def _read_env_app_secret() -> str:
    """从 .env 文本解析 APP_SECRET（跳过注释与占位值）。"""
    try:
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "APP_SECRET":
            v = v.strip()
            if v and "填" not in v and "改" not in v:
                return v
    return ""


def _append_env_app_secret(key: str) -> None:
    """把 APP_SECRET 追加写回 .env；已有该键时不重写（幂等）。"""
    try:
        existing = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
    except OSError:
        existing = ""
    for line in existing.splitlines():
        if line.strip().startswith("APP_SECRET="):
            return  # 已存在，不重写
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"\n# APP_SECRET：敏感值加密密钥（Fernet），首次使用时自动生成\nAPP_SECRET={key}\n"
    _ENV_PATH.write_text(text, encoding="utf-8")
    logger.info("已生成 APP_SECRET 并写入 %s", _ENV_PATH)


def _get_fernet() -> Fernet:
    """取 Fernet 实例；密钥无效时生成新 key 写回 .env。"""
    secret = (getattr(settings, "APP_SECRET", "") or "").strip() or _read_env_app_secret()
    if secret:
        try:
            return Fernet(secret.encode())
        except (ValueError, TypeError) as exc:
            logger.warning("APP_SECRET 不是有效 Fernet key，重新生成: %s", exc)
    key = Fernet.generate_key().decode()
    _append_env_app_secret(key)
    return Fernet(key.encode())


def encrypt_secret(s: str) -> str:
    """明文 -> Fernet 密文（UTF-8 安全）。"""
    return _get_fernet().encrypt(s.encode("utf-8")).decode("utf-8")


def decrypt_secret(s: str) -> str:
    """Fernet 密文 -> 明文；解密失败抛 ValueError。"""
    if not s:
        return ""
    try:
        return _get_fernet().decrypt(s.encode("utf-8")).decode("utf-8")
    except (InvalidToken, TypeError, ValueError) as exc:
        raise ValueError(f"解密失败（密文无效或 APP_SECRET 变更）: {exc}") from exc
