"""MySQL 持久化：PyMySQL 直连、函数式封装（契约 §9）。

get_connection() 每次新建、用完即关；连接失败抛 PersistenceError（带原因），不静默。
所有函数均为同步阻塞，async 调用方须用 asyncio.to_thread 包裹。
"""
from __future__ import annotations

import json
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import settings


class PersistenceError(Exception):
    """持久化异常，携带底层原因。"""


def get_connection() -> pymysql.connections.Connection:
    """新建 MySQL 连接（DictCursor, autocommit=True, charset=utf8mb4）。"""
    try:
        return pymysql.connect(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            database=settings.MYSQL_DATABASE,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=DictCursor,
        )
    except Exception as exc:  # 连接失败必须暴露原因
        raise PersistenceError(f"MySQL 连接失败: {exc}") from exc


def _dump(obj: Any) -> str | None:
    """JSON 字段序列化（ensure_ascii=False 保留中文）。"""
    return None if obj is None else json.dumps(obj, ensure_ascii=False)


# ---------- datasets ----------

def insert_dataset(
    dataset_id: str,
    name: str,
    path: str,
    rows_estimate: int,
    size_bytes: int,
    schema: Any,
) -> None:
    """上传成功后登记数据集元数据。"""
    sql = (
        "INSERT INTO datasets (id, name, path, rows_estimate, size_bytes, schema_json) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (dataset_id, name, path, rows_estimate, size_bytes, _dump(schema)))


def get_dataset(dataset_id: str) -> dict | None:
    """按 id 查数据集；schema_json 已反序列化。"""
    sql = "SELECT id, name, path, rows_estimate, size_bytes, schema_json, created_at FROM datasets WHERE id = %s"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (dataset_id,))
        row = cur.fetchone()
    if row is None:
        return None
    row["schema_json"] = json.loads(row["schema_json"]) if row.get("schema_json") else None
    return row


# ---------- tasks ----------

def insert_task(task_id: str, dataset_id: str | None, query: str, status: str = "created") -> None:
    """任务创建时落一条记录。"""
    sql = "INSERT INTO tasks (id, dataset_id, query, status) VALUES (%s, %s, %s, %s)"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (task_id, dataset_id, query, status))


def update_task(
    task_id: str,
    status: str,
    engine: str | None = None,
    final_result: Any = None,
    error: str | None = None,
) -> None:
    """按需更新任务状态；进入终态时补 completed_at。"""
    sets: list[str] = ["status = %s"]
    params: list[Any] = [status]
    if engine is not None:
        sets.append("engine = %s")
        params.append(engine)
    if final_result is not None:
        sets.append("final_result = %s")
        params.append(_dump(final_result))
    if error is not None:
        sets.append("error = %s")
        params.append(error)
    if status in ("completed", "failed_final"):
        sets.append("completed_at = NOW()")
    params.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def insert_task_step(
    task_id: str,
    step: int,
    agent_name: str,
    status: str,
    latency_ms: int | None = None,
    retry_count: int = 0,
    detail: Any = None,
) -> None:
    """记录一次 agent 步骤（开始 status=running，结束带耗时）。"""
    sql = (
        "INSERT INTO task_steps (task_id, step, agent_name, status, latency_ms, retry_count, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (task_id, step, agent_name, status, latency_ms, retry_count, _dump(detail)))


def insert_agent_run(
    task_id: str,
    agent_name: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    tool_name: str | None = None,
    error_type: str | None = None,
) -> None:
    """记录一次 LLM/agent 调用观测数据。"""
    sql = (
        "INSERT INTO agent_runs (task_id, agent_name, model, input_tokens, output_tokens, "
        "latency_ms, tool_name, error_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (task_id, agent_name, model, input_tokens, output_tokens, latency_ms, tool_name, error_type))


# ---------- jobs（爬虫） ----------

def upsert_job(
    title: str,
    company: str | None,
    location: str | None,
    skills: Any,
    description: str | None,
    source_url: str,
) -> str:
    """按 uq_job 去重写入；返回 "inserted" 或 "skipped"。"""
    sql = (
        "INSERT INTO jobs (title, company, location, skills, description, source_url) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE crawled_at = NOW()"
    )
    skills_str = ",".join(skills) if isinstance(skills, (list, tuple)) else skills
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (title, company, location, skills_str, description, source_url))
        # rowcount: 1=新插入, 2/0=命中唯一键走 UPDATE 分支
        return "inserted" if cur.rowcount == 1 else "skipped"


def list_jobs(limit: int = 20) -> list[dict]:
    """按时间倒序列出岗位。"""
    sql = "SELECT id, title, company, location, skills, description, source_url, crawled_at FROM jobs ORDER BY id DESC LIMIT %s"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (limit,))
        return list(cur.fetchall())


def count_jobs() -> int:
    """岗位总数。"""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM jobs")
        row = cur.fetchone()
    return int(row["c"]) if row else 0


def fetch_jobs_for_export() -> list[dict]:
    """导出 CSV 用：全量岗位五列。"""
    sql = "SELECT title, company, location, skills, description FROM jobs ORDER BY id ASC"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())
