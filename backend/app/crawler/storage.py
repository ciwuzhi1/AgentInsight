"""岗位入库：逐条调用 persistence.mysql.upsert_job（契约 §12）。"""
from __future__ import annotations

from app.persistence import mysql


def upsert_one(item: dict) -> str:
    """入库单条岗位；返回 "inserted" 或 "skipped"；PersistenceError 向上传播。

    成功一条写一条，调用方无需等待整批抓完。
    """
    return mysql.upsert_job(
        title=item["title"],
        company=item.get("company"),
        location=item.get("location"),
        skills=item.get("skills"),
        description=item.get("description"),
        source_url=item.get("detail_url"),
    )


def upsert_jobs(items: list[dict]) -> dict:
    """逐条去重写入 jobs 表，返回 {inserted, skipped}；PersistenceError 向上传播。

    本函数内部逐条 upsert_one；单条失败由调用方决定是否记入 failed_urls。
    """
    inserted = skipped = 0
    for item in items:
        if upsert_one(item) == "inserted":
            inserted += 1
        else:
            skipped += 1
    return {"inserted": inserted, "skipped": skipped}
