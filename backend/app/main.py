"""FastAPI 入口：路由装配、CORS、全局兜底与访问日志（契约 §8；CONTRACTS2 §5）。"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api import agent, auth, datasets, health
from app.core.config import settings
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

# CORS 收紧（CONTRACTS3 §3.4）：白名单来源来自 config.CORS_ORIGINS（逗号分隔）
_CORS_ORIGINS = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS or ["*"],
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

# 阶段3 增量（CONTRACTS3 §3.3）：注册 / 登录 / me
app.include_router(auth.router)
from app.api import crawler  # CODE-7 并行产出， noqa: E402

app.include_router(crawler.router)

# 阶段2 增量（CONTRACTS2 §4.6）：简历 / 匹配 / 模型配置 / 设置中心
from app.api import matches, models as models_api, resumes, settings as settings_api  # noqa: E402

app.include_router(resumes.router)
app.include_router(matches.router)
app.include_router(models_api.router)
app.include_router(settings_api.router)

# 阶段3 增量（CONTRACTS3 §2.4）：评测报告只读接口
from app.api import evaluation  # noqa: E402

app.include_router(evaluation.router)


@app.get("/")
async def root() -> HTMLResponse:
    """人类入口：简明导航页（API 客户端请用 /api/* 与 /docs）。"""
    return HTMLResponse(
        "<html><head><meta charset='utf-8'><title>AgentInsight Backend</title></head>"
        "<body style='font-family:system-ui;max-width:560px;margin:80px auto;line-height:1.9'>"
        "<h2>AgentInsight 后端（API 服务）</h2>"
        "<p>这里是 API，没有界面。界面（前端）在 <a href='http://localhost:3100'>http://localhost:3100</a></p>"
        "<ul>"
        "<li>接口文档（Swagger）：<a href='/docs'>/docs</a></li>"
        "<li>健康检查：<a href='/api/health'>/api/health</a> · "
        "<a href='/api/health/mysql'>/api/health/mysql</a> · "
        "<a href='/api/health/redis'>/api/health/redis</a></li>"
        "</ul></body></html>"
    )
