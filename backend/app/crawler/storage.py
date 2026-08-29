"""岗位入库：逐条调用 persistence.mysql.upsert_job（契约 §12）。"""
from __future__ import annotations

from app.persistence import mysql


def upsert_jobs(items: list[dict]) -> dict:
    """逐条去重写入 jobs 表，返回 {inserted, skipped}；PersistenceError 向上传播。"""
    inserted = skipped = 0
    for item in items:
        result = mysql.upsert_job(
            title=item["title"],
            company=item.get("company"),
            location=item.get("location"),
            skills=item.get("skills"),
            description=item.get("description"),
            source_url=item["detail_url"],
        )
        if result == "inserted":
            inserted += 1
        else:
            skipped += 1
    return {"inserted": inserted, "skipped": skipped}
