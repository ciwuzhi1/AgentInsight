"""MySQL 持久化：PyMySQL 直连、函数式封装（契约 §9）。

get_connection() 每次新建、用完即关；连接失败抛 PersistenceError（带原因），不静默。
所有函数均为同步阻塞，async 调用方须用 asyncio.to_thread 包裹。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pymysql
from pymysql.cursors import DictCursor
from pymysql.err import OperationalError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class PersistenceError(Exception):
    """持久化异常，携带底层原因。"""


def get_connection() -> pymysql.connections.Connection:
    """新建 MySQL 连接（DictCursor, autocommit=True, charset=utf8mb4）。

    健壮性（CONTRACTS2 §4.2）：connect/read/write 超时；OperationalError
    （连接瞬断/超时）单次重连重试，其余异常直接抛 PersistenceError。
    """
    last_exc: Exception | None = None
    for attempt in range(2):
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
                connect_timeout=5,
                read_timeout=15,
                write_timeout=15,
            )
        except OperationalError as exc:
            last_exc = exc
            logger.warning("MySQL 连接失败(第 %d 次, 重试): %s", attempt + 1, exc)
        except Exception as exc:  # 非瞬时错误（认证/库不存在等）不重试
            raise PersistenceError(f"MySQL 连接失败: {exc}") from exc
    raise PersistenceError(f"MySQL 连接失败: {last_exc}") from last_exc


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
    user_id: str | None = None,
) -> None:
    """上传成功后登记数据集元数据（user_id 为空表示公共遗留，任何登录用户可见）。"""
    sql = (
        "INSERT INTO datasets (id, name, path, rows_estimate, size_bytes, schema_json, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (dataset_id, name, path, rows_estimate, size_bytes, _dump(schema), user_id))


def get_dataset(dataset_id: str) -> dict | None:
    """按 id 查数据集；schema_json 已反序列化（user_id 由 API 层做隔离校验）。"""
    sql = "SELECT id, name, path, rows_estimate, size_bytes, schema_json, user_id, created_at FROM datasets WHERE id = %s"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (dataset_id,))
        row = cur.fetchone()
    if row is None:
        return None
    row["schema_json"] = json.loads(row["schema_json"]) if row.get("schema_json") else None
    return row


# ---------- tasks ----------

def insert_task(
    task_id: str,
    dataset_id: str | None,
    query: str,
    status: str = "created",
    user_id: str | None = None,
) -> None:
    """任务创建时落一条记录（user_id 为空表示公共遗留）。"""
    sql = "INSERT INTO tasks (id, dataset_id, query, status, user_id) VALUES (%s, %s, %s, %s, %s)"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (task_id, dataset_id, query, status, user_id))


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


# ---------- resumes（简历，CONTRACTS2 §4.2）----------

def save_resume(resume_id: str, filename: str, path: str, profile: dict, user_id: str | None = None) -> None:
    """登记简历（上传时 profile 传空占位，解析成功后覆写；user_id 为空表示公共遗留）。"""
    sql = (
        "INSERT INTO resumes (id, filename, path, profile_json, user_id) VALUES (%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE filename = VALUES(filename), path = VALUES(path), "
        "profile_json = VALUES(profile_json), user_id = VALUES(user_id)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (resume_id, filename, path, _dump(profile), user_id))


def get_resume(resume_id: str) -> dict | None:
    """按 id 查简历；profile_json 已反序列化（user_id 由 API 层做隔离校验）。"""
    sql = "SELECT id, filename, path, profile_json, user_id, created_at FROM resumes WHERE id = %s"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (resume_id,))
        row = cur.fetchone()
    if row is None:
        return None
    row["profile_json"] = json.loads(row["profile_json"]) if row.get("profile_json") else {}
    return row


# ---------- matches（匹配结果）----------

def insert_match(
    task_id: str,
    resume_id: str | None,
    job_ids: list,
    score,
    detail: dict,
    user_id: str | None = None,
) -> None:
    """匹配任务 final 事件落一条结果（api/agent.py 钩子调用，签名见 §5.1）。"""
    sql = (
        "INSERT INTO matches (task_id, resume_id, job_ids_json, score, detail_json, user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (task_id, resume_id, _dump(list(job_ids or [])), score, _dump(detail), user_id))


# ---------- users（CONTRACTS3 §3.1）----------

def create_user(user_id: str, username: str, password_hash: str) -> bool:
    """注册用户；用户名唯一键冲突返回 False（其余异常向上抛）。"""
    sql = "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s)"
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (user_id, username, password_hash))
    except pymysql.err.IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:  # Duplicate entry
            return False
        raise
    return True


def get_user_by_username(username: str) -> dict | None:
    """按用户名查用户（登录用）；无则 None。"""
    sql = "SELECT id, username, password_hash, created_at FROM users WHERE username = %s"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (username,))
        return cur.fetchone()


# ---------- jobs 按 id 查（匹配链路用）----------

def get_jobs_by_ids(ids: list) -> list[dict]:
    """按传入 id 顺序返回岗位 [{id,title,company,location,skills,description}]。"""
    uniq = [i for i in dict.fromkeys(ids or []) if i is not None]
    if not uniq:
        return []
    sql = (
        "SELECT id, title, company, location, skills, description FROM jobs "
        f"WHERE id IN ({', '.join(['%s'] * len(uniq))})"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, uniq)
        rows = {row["id"]: row for row in cur.fetchall()}
    return [rows[i] for i in uniq if i in rows]


# ---------- model_configs（模型配置）----------

def list_model_configs() -> list[dict]:
    """全部模型配置（api_key_enc 为密文，脱敏在 API 层做）。"""
    sql = (
        "SELECT id, name, provider, base_url, api_key_enc, model, temperature, "
        "is_active, updated_at FROM model_configs ORDER BY updated_at DESC"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def get_active_model_config() -> dict | None:
    """当前激活的唯一配置；无则 None。"""
    sql = (
        "SELECT id, name, provider, base_url, api_key_enc, model, temperature, "
        "is_active, updated_at FROM model_configs WHERE is_active = 1 LIMIT 1"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()


def save_model_config(cfg: dict) -> str:
    """新增模型配置（api_key 已在 API 层加密）；返回配置 id。"""
    cfg_id = cfg.get("id") or uuid.uuid4().hex
    sql = (
        "INSERT INTO model_configs (id, name, provider, base_url, api_key_enc, model, "
        "temperature, is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                cfg_id,
                cfg.get("name"),
                cfg.get("provider"),
                cfg.get("base_url"),
                cfg.get("api_key_enc"),
                cfg.get("model"),
                cfg.get("temperature", 0),
                cfg.get("is_active", 0),
            ),
        )
    return cfg_id


def activate_model_config(cfg_id: str) -> None:
    """事务内先清全部 is_active，再置目标行为 1（保证唯一 active）。"""
    conn = get_connection()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("UPDATE model_configs SET is_active = 0 WHERE is_active = 1")
            cur.execute("UPDATE model_configs SET is_active = 1 WHERE id = %s", (cfg_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_model_config(cfg_id: str) -> None:
    """删除模型配置。"""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM model_configs WHERE id = %s", (cfg_id,))


# ---------- app_settings（设置中心）----------

def get_all_settings() -> dict[str, dict]:
    """全部设置：{key: {"value": 原文(密文若 secret), "is_secret": bool}}。"""
    sql = "SELECT `key`, value, is_secret FROM app_settings"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {r["key"]: {"value": r["value"], "is_secret": bool(r["is_secret"])} for r in rows}


def upsert_setting(key: str, value: str, is_secret: bool = False) -> None:
    """按 key 写入设置（存在则覆盖 value 与 is_secret）。"""
    sql = (
        "INSERT INTO app_settings (`key`, value, is_secret) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value), is_secret = VALUES(is_secret)"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (key, value, 1 if is_secret else 0))
