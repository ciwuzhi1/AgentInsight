"""FastAPI 入口：路由装配、CORS、全局兜底与访问日志（契约 §8；CONTRACTS2 §5）。"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import agent, datasets, health
from app.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 拉起 TaskBus 完结任务 TTL 清扫（幂等，仅一次）
    agent.bus.start_sweeper()
    logger.info("AgentInsight backend 启动")
    yield
    logger.info("AgentInsight backend 已关闭")


app = FastAPI(title="AgentInsight", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局兜底：未处理异常统一 500，不向客户端泄露堆栈。"""
    logger.exception("未处理异常 path=%s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """访问日志：method / path / 耗时 / 状态码。"""
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

app.include_router(datasets.router)
app.include_router(agent.router)
app.include_router(health.router)
from app.api import crawler  # CODE-7 并行产出， noqa: E402

app.include_router(crawler.router)

# 阶段2 增量（CONTRACTS2 §4.6）：简历 / 匹配 / 模型配置 / 设置中心
from app.api import matches, models as models_api, resumes, settings as settings_api  # noqa: E402

app.include_router(resumes.router)
app.include_router(matches.router)
app.include_router(models_api.router)
app.include_router(settings_api.router)


@app.get("/")
async def root() -> dict:
    return {"name": "AgentInsight", "docs": "/docs"}
