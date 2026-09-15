"""模型配置 API：模型列表/新增/激活/删除/连通性测试（CONTRACTS2 §4.6）。

加密统一在 API 层做（mysql 存密文字符串）；对外仅返回脱敏 key。
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import UserCtx, get_current_user
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.llm import LLMError, OpenAICompatibleClient
from app.core.logging import get_logger
from app.persistence.mysql import (
    activate_model_config,
    delete_model_config,
    list_model_configs,
    save_model_config,
)

router = APIRouter(prefix="/api/models", tags=["models"])
logger = get_logger(__name__)


class ModelConfigIn(BaseModel):
    name: str
    provider: str = "openai"
    base_url: str
    api_key: str = Field(min_length=1)
    model: str
    temperature: float = 0.0


def _mask(key_enc: str) -> str:
    """密文解密后脱敏为 `***尾4位`；空/解密失败显示空串。"""
    if not key_enc:
        return ""
    try:
        plain = decrypt_secret(key_enc)
    except ValueError:
        return ""
    return "" if not plain else f"***{plain[-4:]}"


def _public_view(row: dict) -> dict:
    """把模型配置行转为对外视图（api_key 脱敏）。"""
    return {
        "id": row["id"],
        "name": row.get("name"),
        "provider": row.get("provider"),
        "base_url": row.get("base_url"),
        "model": row.get("model"),
        "temperature": row.get("temperature"),
        "is_active": bool(row.get("is_active")),
        "api_key_masked": _mask(row.get("api_key_enc") or ""),
    }


@router.get("")
async def list_models(user: UserCtx = Depends(get_current_user)) -> list[dict]:
    """模型配置列表（api_key 脱敏）。"""
    return await asyncio.to_thread(lambda: [_public_view(r) for r in list_model_configs()])


@router.post("")
async def create_model(body: ModelConfigIn, user: UserCtx = Depends(get_current_user)) -> dict:
    """新增模型配置（api_key 加密落库，默认不激活）。"""
    cfg = {
        "id": uuid.uuid4().hex,
        "name": body.name,
        "provider": body.provider,
        "base_url": body.base_url,
        "api_key_enc": encrypt_secret(body.api_key),
        "model": body.model,
        "temperature": body.temperature,
        "is_active": 0,
    }
    cfg_id = await asyncio.to_thread(save_model_config, cfg)
    return {"id": cfg_id, "name": body.name, "status": "created"}


@router.put("/{model_id}/activate")
async def activate(model_id: str, user: UserCtx = Depends(get_current_user)) -> dict:
    """激活指定模型（事务内先清后设，保证唯一 active）。"""
    rows = await asyncio.to_thread(list_model_configs)
    if not any(r["id"] == model_id for r in rows):
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_id}")
    await asyncio.to_thread(activate_model_config, model_id)
    # 激活后立即刷新 get_llm_client 的配置缓存由其自身 TTL 兜底，这里只返回结果
    return {"id": model_id, "is_active": True}


@router.delete("/{model_id}")
async def remove(model_id: str, user: UserCtx = Depends(get_current_user)) -> dict:
    """删除模型配置；不存在 404。"""
    rows = await asyncio.to_thread(list_model_configs)
    if not any(r["id"] == model_id for r in rows):
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_id}")
    await asyncio.to_thread(delete_model_config, model_id)
    return {"id": model_id, "deleted": True}


@router.post("/{model_id}/test")
async def test_model(model_id: str, user: UserCtx = Depends(get_current_user)) -> dict:
    """连通性测试：临时构造 client 发 1 次 JSON 请求，返回 ok/latency_ms/error。"""
    rows = await asyncio.to_thread(list_model_configs)
    row = next((r for r in rows if r["id"] == model_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"模型配置不存在: {model_id}")
    try:
        api_key = decrypt_secret(row.get("api_key_enc") or "")
    except ValueError as exc:
        return {"ok": False, "error": f"api_key 解密失败: {exc}"}

    client = OpenAICompatibleClient(
        base_url=row.get("base_url"),
        api_key=api_key,
        model=row.get("model"),
        temperature=row.get("temperature"),
    )
    t0 = time.perf_counter()
    try:
        await client.generate_json(
            "你是连通性测试助手，收到任何消息都只输出 JSON 对象 {\"pong\": true}",
            "ping",
        )
        return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000)}
    except LLMError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # 兜底：测试接口自身不能 500
        return {"ok": False, "error": f"测试失败: {exc}"}
