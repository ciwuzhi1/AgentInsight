"""FastAPI 入口：路由装配与 CORS（契约 §8）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, datasets, health
from app.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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

app.include_router(datasets.router)
app.include_router(agent.router)
app.include_router(health.router)
from app.api import crawler  # CODE-7 并行产出， noqa: E402

app.include_router(crawler.router)


@app.get("/")
async def root() -> dict:
    return {"name": "AgentInsight", "docs": "/docs"}
