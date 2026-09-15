"""运行时设置中心：app_settings 表的读取/写入与脱敏（CONTRACTS2 §4.4）。

- get_setting：进程内 TTL 缓存 10s；secret 行解密后返回；DB 不可用回退内置默认值并 warning。
- set_setting：写库 + 刷新缓存（secret 在写库前加密，解密只发生在读取侧）。
- get_all_masked：全量脱敏视图（secret 显示 `***尾4位`，值为空显示 ""）。
"""
from __future__ import annotations

import time

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.logging import get_logger
from app.persistence.mysql import get_all_settings, upsert_setting

logger = get_logger(__name__)

# 内置默认表（与 schema.sql 内置行一致）：key -> (默认值, 是否 secret)
_DEFAULTS: dict[str, tuple[str, bool]] = {
    "match_llm_enabled": ("true", False),
    "llm_fallback_mock": ("auto", False),
    "parser_backend": ("mineru_api", False),
    "sql_timeout": ("30", False),
    "mineru_api_token": ("", True),
    "tavily_api_key": ("", True),
}

# key -> 默认值（公开给降级场景使用）
DEFAULT_SETTINGS: dict[str, str] = {k: v for k, (v, _) in _DEFAULTS.items()}

_TTL_S = 10.0
# get_setting 的 TTL 缓存：key -> (monotonic 时间戳, 值)
_cache: dict[str, tuple[float, str]] = {}


def _mask(value: str) -> str:
    """secret 脱敏：`***` + 尾4位；空值显示空串。"""
    return "" if not value else f"***{value[-4:]}"


def _fetch_rows() -> dict[str, dict]:
    """读全量设置行；DB 不可用时抛异常由调用方降级。"""
    return get_all_settings()


def get_setting(key: str, default: str | None = None) -> str:
    """按 key 取设置值（secret 自动解密）；带 10s TTL 缓存。

    DB 不可用时回退内置默认值（其次 default）并 warning，不阻断调用方。
    """
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _TTL_S:
        return hit[1]

    try:
        rows = _fetch_rows()
    except Exception as exc:
        logger.warning("app_settings 读取失败，回退默认值 key=%s: %s", key, exc)
        value = DEFAULT_SETTINGS.get(key, "" if default is None else default)
        return value

    row = rows.get(key)
    if row is None:
        value = DEFAULT_SETTINGS.get(key, "" if default is None else default)
    else:
        value = row["value"] or ""
        if row["is_secret"] and value:
            try:
                value = decrypt_secret(value)
            except ValueError as exc:
                logger.warning("app_settings secret 解密失败 key=%s: %s", key, exc)
                value = ""
    _cache[key] = (now, value)
    return value


async def get_setting_async(key: str, default: str | None = None) -> str:
    """异步版本：get_setting 的 to_thread 包装，避免阻塞事件循环。"""
    import asyncio

    return await asyncio.to_thread(get_setting, key, default)


def set_setting(key: str, value: str, is_secret: bool = False) -> None:
    """写入设置（secret 加密落库）并刷新缓存。"""
    stored = encrypt_secret(value) if (is_secret and value) else value
    upsert_setting(key, stored, is_secret)
    _cache[key] = (time.monotonic(), value)


def get_all_masked() -> dict[str, str]:
    """全量设置脱敏视图；含未落库的内置默认键。DB 不可用回退默认值。"""
    try:
        rows = _fetch_rows()
    except Exception as exc:
        logger.warning("app_settings 全量读取失败，回退默认视图: %s", exc)
        rows = {}
        for key, (value, is_secret) in _DEFAULTS.items():
            rows[key] = {"value": value, "is_secret": is_secret}

    result: dict[str, str] = {}
    for key in dict.fromkeys(list(_DEFAULTS) + list(rows)):
        row = rows.get(key)
        if row is None:
            value, is_secret = _DEFAULTS[key]
        else:
            value, is_secret = row["value"] or "", row["is_secret"]
        if is_secret:
            if value:
                try:
                    value = decrypt_secret(value)
                except ValueError:
                    value = ""
            result[key] = _mask(value)
        else:
            result[key] = value
    return result
