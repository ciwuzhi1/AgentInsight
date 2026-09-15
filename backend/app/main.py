"""FastAPI 入口：路由装配、CORS、可观测性（request_id/指标）、全局兜底（企业级）。"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api import agent, auth, datasets, health
from app.core.config import settings
from app.core.logging import get_logger, get_request_id, set_request_id

logger = get_logger(__name__)

# ---------- 轻量指标（单实例；多实例可换 Prometheus client） ----------
_METRICS = {"requests_total": 0, "errors_total": 0, "latency_sum_ms": 0.0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 拉起 TaskBus 完结任务 TTL 清扫（幂等，仅一次）
    agent.bus.start_sweeper()
    # 启动即迁移（幂等；失败不阻塞启动，只告警——可用 /api/health/mysql 观测）
    try:
        import asyncio
        from app.persistence.migrations import run_migrations

        applied = await asyncio.to_thread(run_migrations)
        if applied:
            logger.info("数据库迁移完成", extra={"extra": {"applied": applied}})
    except Exception as exc:
        logger.warning("数据库迁移失败（不影响启动）: %s", exc)
    # 清理残留幂等锁（重启后锁可能仍持有，阻止用户重新提交）
    try:
        from app.cache.redis import cleanup_stale_locks

        cleaned = await cleanup_stale_locks()
        if cleaned:
            logger.info("启动清理残留锁 %d 个", cleaned)
    except Exception as exc:
        logger.warning("清理残留锁失败（不影响启动）: %s", exc)
    logger.info("AgentInsight backend 启动")
    yield
    # 关闭时释放 Redis 单例连接 + DuckDB 连接
    try:
        from app.cache.redis import close_redis

        await close_redis()
    except Exception:
        pass
    try:
        from app.data_engine.duckdb_engine import duckdb_engine

        duckdb_engine.close_all()
    except Exception:
        pass
    logger.info("AgentInsight backend 已关闭")


app = FastAPI(title="AgentInsight", lifespan=lifespan)

# CORS 收紧（CONTRACTS3 §3.4）：白名单来源来自 config.CORS_ORIGINS（逗号分隔）
# 生产环境禁止通配符：CORS_ORIGINS 为空时拒绝启动，防止跨域攻击
_CORS_ORIGINS = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if not _CORS_ORIGINS:
    logger.error("CORS_ORIGINS 未配置，已拒绝启动。请在 .env 中设置 CORS_ORIGINS=http://localhost:3100")
    raise RuntimeError("CORS_ORIGINS must not be empty in production")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局兜底：未处理异常统一 500，不向客户端泄露堆栈；返回 request_id 便于排障。"""
    logger.exception("未处理异常 path=%s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "request_id": get_request_id()},
    )


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """request_id 注入 + 结构化访问日志 + 指标计数。"""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    set_request_id(rid)
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _METRICS["requests_total"] += 1
    _METRICS["latency_sum_ms"] += elapsed_ms
    if response.status_code >= 500:
        _METRICS["errors_total"] += 1
    response.headers["X-Request-ID"] = rid
    logger.info(
        "access",
        extra={"extra": {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": round(elapsed_ms, 1),
        }},
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


@app.get("/healthz")
async def healthz_root() -> dict:
    """存活探针（根路径别名）。"""
    return {"status": "alive"}


@app.get("/readyz")
async def readyz_root() -> dict:
    """就绪探针（根路径别名）：复用 /api/health/ready 逻辑。"""
    from app.api.health import readyz as _readyz

    return await _readyz()


@app.get("/metrics")
async def metrics() -> dict:
    """轻量运行指标（企业级可观测性；多实例可换 Prometheus client）。"""
    avg = _METRICS["latency_sum_ms"] / _METRICS["requests_total"] if _METRICS["requests_total"] else 0
    return {
        "requests_total": _METRICS["requests_total"],
        "errors_total": _METRICS["errors_total"],
        "avg_latency_ms": round(avg, 1),
        "active_tasks": len(agent.bus.snapshot_all()) if hasattr(agent.bus, "snapshot_all") else None,
    }


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
        "<li>健康探针：<a href='/healthz'>/healthz</a>（存活）· <a href='/readyz'>/readyz</a>（就绪）</li>"
        "<li>指标：<a href='/metrics'>/metrics</a></li>"
        "</ul></body></html>"
    )
