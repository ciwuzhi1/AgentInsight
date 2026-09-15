"""设置中心 API：全量脱敏读取与白名单键更新（CONTRACTS2 §4.6）。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import UserCtx, get_current_user
from app.core.app_settings import get_all_masked, set_setting
from app.core.logging import get_logger

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = get_logger(__name__)

# 可写键白名单（与内置默认表一致）；secret 键写库前自动加密
ALLOWED_KEYS = {
    "match_llm_enabled",
    "report_llm_enabled",
    "llm_fallback_mock",
    "parser_backend",
    "sql_timeout",
    "mineru_api_token",
    "tavily_api_key",
}
_SECRET_KEYS = {"mineru_api_token", "tavily_api_key"}


class SettingUpdateIn(BaseModel):
    key: str
    value: str


@router.get("")
async def read_settings(user: UserCtx = Depends(get_current_user)) -> dict:
    """全量设置（secret 脱敏为 `***尾4位`，空值显示 ""）。"""
    return await asyncio.to_thread(get_all_masked)


@router.put("")
async def update_setting(body: SettingUpdateIn, user: UserCtx = Depends(get_current_user)) -> dict:
    """更新单个设置；非白名单键 400。"""
    if body.key not in ALLOWED_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的设置键: {body.key}（可选: {', '.join(sorted(ALLOWED_KEYS))}）",
        )
    await asyncio.to_thread(set_setting, body.key, body.value, body.key in _SECRET_KEYS)
    return {"key": body.key, "updated": True}
