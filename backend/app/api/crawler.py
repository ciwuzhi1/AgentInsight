"""爬虫 API：/api/crawler 三路由（契约 §8、§12）。

抓取为阻塞 requests，统一 asyncio.to_thread 包裹；每请求 sleep 0.5s 限速。
"""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger
from app.crawler import storage
from app.crawler.fetcher import FetchError, fetch
from app.crawler.parser import (
    BASE_URL,
    extract_skills,
    listing_url,
    parse_detail,
    parse_listing,
)
from app.persistence.mysql import (
    PersistenceError,
    count_jobs,
    fetch_jobs_for_export,
    list_jobs,
)

logger = get_logger("app.crawler")

router = APIRouter(prefix="/api/crawler", tags=["crawler"])
# 独立 /api/jobs 路由（岗位列表，分页）；与 crawler 运行控制分离
jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# 每次请求后的礼貌间隔（秒）
CRAWL_INTERVAL = 0.5


class CrawlerRunRequest(BaseModel):
    """POST /run 请求体；url 留空用默认演示站。"""

    url: str | None = None
    pages: int = Field(default=1, ge=1, le=20)
    max_items: int = Field(default=50, ge=1, le=500)


def _page_url(base: str, page: int) -> str:
    """自定义站点分页：page=1 用原 URL，>1 拼 page/{n}/。"""
    if page <= 1:
        return base
    return f"{base.rstrip('/')}/page/{page}/"


async def run(url: str | None, pages: int, max_items: int) -> dict:
    """抓取 → 解析 → 入库 的核心流程，供路由与测试复用。"""
    base = url or BASE_URL
    items: list[dict] = []

    for page in range(1, pages + 1):
        if len(items) >= max_items:
            break
        page_url = _page_url(base, page) if url else listing_url(page)
        html = await asyncio.to_thread(fetch, page_url)
        page_items = parse_listing(html, page_url)
        if not page_items:
            break
        for item in page_items:
            if len(items) >= max_items:
                break
            description = ""
            if item.get("detail_url"):
                await asyncio.sleep(CRAWL_INTERVAL)
                detail_html = await asyncio.to_thread(fetch, item["detail_url"])
                description = parse_detail(detail_html)
            item["description"] = description
            item["skills"] = extract_skills(f"{item['title']} {description}")
            items.append(item)
        if page < pages:
            await asyncio.sleep(CRAWL_INTERVAL)

    result = await asyncio.to_thread(storage.upsert_jobs, items)
    result["items"] = [
        {
            "title": it["title"],
            "company": it.get("company"),
            "location": it.get("location"),
            "skills": it.get("skills", []),
        }
        for it in items
    ]
    return result


@router.post("/run")
async def run_crawler(req: CrawlerRunRequest) -> dict:
    """抓取演示站岗位并落 MySQL。"""
    try:
        return await run(req.url, req.pages, req.max_items)
    except FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {exc}") from exc


@router.get("/jobs")
async def get_jobs(limit: int = Query(default=20, ge=1, le=500)) -> dict:
    """列出已入库岗位。"""
    try:
        rows = await asyncio.to_thread(list_jobs, limit)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {exc}") from exc
    return {"count": len(rows), "items": rows}


@jobs_router.get("")
async def list_jobs_paginated(
    limit: int = Query(default=20, ge=1, le=100),
    page: int = Query(default=1, ge=1),
) -> dict:
    """岗位列表（分页：page 从 1 起；按 id 倒序）。"""
    limit_n = max(1, min(limit, 100))
    page_n = max(1, page)
    offset = (page_n - 1) * limit_n
    try:
        rows = await asyncio.to_thread(list_jobs, limit_n, offset)
        total = await asyncio.to_thread(count_jobs)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {exc}") from exc
    return {
        "page": page_n,
        "limit": limit_n,
        "count": len(rows),
        "total": total,
        "items": rows,
        "has_more": offset + len(rows) < total,
    }


@router.post("/export")
async def export_jobs() -> dict:
    """导出 jobs 表到 CSV。"""
    try:
        rows = await asyncio.to_thread(fetch_jobs_for_export)
    except PersistenceError as exc:
        raise HTTPException(status_code=503, detail=f"MySQL 不可用: {exc}") from exc

    out_dir = Path(settings.data_dir) / "large"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "jd_crawled.csv"

    def _write() -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["title", "company", "location", "skills", "description"])
            for row in rows:
                skills = row.get("skills") or ""
                writer.writerow(
                    [
                        row.get("title") or "",
                        row.get("company") or "",
                        row.get("location") or "",
                        skills,
                        (row.get("description") or "").replace("\n", " "),
                    ]
                )

    await asyncio.to_thread(_write)
    # 统一正斜杠，方便前端展示
    return {"path": str(path).replace("\\", "/"), "rows": len(rows)}
