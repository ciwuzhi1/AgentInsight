"""认证 API：注册 / 登录 / me + get_current_user 依赖（CONTRACTS3 §3.3）。

- POST /api/auth/register：用户名 3~32 字符唯一、密码 ≥6；重名 409；成功 201。
- POST /api/auth/login：校验密码，签发 HS256 token（exp 7d）；错用户名/密码 401。
- GET  /api/auth/me：Bearer token 返回当前用户。
- get_current_user：FastAPI Depends 依赖，解析 Authorization: Bearer → UserCtx；
  缺失/无效一律 401。应用范围（本轮）：/api/datasets、/api/resumes、/api/matches、
  /api/tasks（含 SSE/详情）；/api/auth/*、/api/health*、/api/crawler/*、/api/settings、
  /api/models、/api/evaluations/* 保持开放。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.persistence.mysql import create_user, get_user_by_username

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)


@dataclass
class UserCtx:
    """当前请求的已登录用户（get_current_user 注入）。"""

    user_id: str
    username: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserCtx:
    """解析 Bearer token → UserCtx；缺失/无效一律 401（不泄露细节差异）。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="未登录：缺少 Authorization Bearer token")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="token 无效或已过期") from None
    return UserCtx(user_id=payload["user_id"], username=payload["username"])


async def get_current_user_flex(
    request: Request,
    token: str | None = None,
) -> UserCtx:
    """SSE 专用：EventSource 无法携带 header，允许 ?token= 查询参数兜底。"""
    auth = request.headers.get("authorization") or ""
    cred = token or (auth[7:] if auth.lower().startswith("bearer ") else None)
    if not cred:
        raise HTTPException(status_code=401, detail="未登录：缺少 token")
    try:
        payload = decode_token(cred)
    except ValueError:
        raise HTTPException(status_code=401, detail="token 无效或已过期") from None
    return UserCtx(user_id=payload["user_id"], username=payload["username"])


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201)
async def register(body: RegisterRequest) -> dict:
    """注册：用户名 3~32 字符唯一，密码 ≥6；重名 409。"""
    username = body.username.strip()
    if not (3 <= len(username) <= 32):
        raise HTTPException(status_code=400, detail="用户名长度需为 3~32 字符")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    password_hash = await asyncio.to_thread(hash_password, body.password)
    user_id = uuid.uuid4().hex
    try:
        ok = await asyncio.to_thread(create_user, user_id, username, password_hash)
    except Exception as exc:
        logger.warning("注册落库失败 username=%s: %s", username, exc)
        raise HTTPException(status_code=503, detail="数据库不可用，请稍后重试") from exc
    if not ok:  # 唯一键冲突
        raise HTTPException(status_code=409, detail="用户名已存在")
    return {"user_id": user_id, "username": username}


@router.post("/login")
async def login(body: LoginRequest) -> dict:
    """登录：用户名或密码错误统一 401（不泄露存在性）。"""
    try:
        row = await asyncio.to_thread(get_user_by_username, body.username.strip())
    except Exception as exc:
        logger.warning("登录查询失败 username=%s: %s", body.username, exc)
        raise HTTPException(status_code=503, detail="数据库不可用，请稍后重试") from exc
    if row is None or not verify_password(body.password, row.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "token": create_token(row["id"], row["username"]),
        "user_id": row["id"],
        "username": row["username"],
    }


@router.get("/me")
async def me(user: UserCtx = Depends(get_current_user)) -> dict:
    """当前登录用户信息。"""
    return {"user_id": user.user_id, "username": user.username}
