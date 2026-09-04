"""版本化数据库迁移（企业级：schema 变更可追溯、幂等重放）。

约定：每个迁移是一个 (版本号, 描述, [语句列表])；已应用版本记录在 schema_migrations。
MySQL 8 无 CREATE INDEX IF NOT EXISTS，用 information_schema 预检实现幂等。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.persistence.mysql import get_connection

logger = get_logger(__name__)


def _index_exists(cur, table: str, index: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
        (table, index),
    )
    return bool(cur.fetchone()["n"])


def _add_index(cur, table: str, index: str, columns: str) -> str:
    if _index_exists(cur, table, index):
        return f"-- skip {index} (exists)"
    cur.execute(f"ALTER TABLE `{table}` ADD INDEX `{index}` ({columns})")  # columns 形如 "user_id, created_at"
    return f"add {table}.{index} ({columns})"


# 版本 → (描述, 语句工厂)；语句工厂拿 cursor 做存在性预检，返回执行摘要
MIGRATIONS: list[tuple[int, str, object]] = [
    (
        1,
        "企业级基线索引：用户维度查询与历史排序",
        lambda cur: [
            _add_index(cur, "tasks", "idx_tasks_user_created", "user_id, created_at"),
            _add_index(cur, "tasks", "idx_tasks_created", "created_at"),
            _add_index(cur, "datasets", "idx_datasets_user", "user_id"),
            _add_index(cur, "resumes", "idx_resumes_user", "user_id"),
            _add_index(cur, "matches", "idx_matches_resume", "resume_id"),
            _add_index(cur, "jobs", "idx_jobs_crawled", "crawled_at"),
        ],
    ),
]


def run_migrations() -> list[str]:
    """应用所有未执行的迁移，返回摘要列表（幂等，可重复调用）。"""
    applied: list[str] = []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INT PRIMARY KEY, description VARCHAR(255), "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        cur.execute("SELECT version FROM schema_migrations")
        done = {r["version"] for r in cur.fetchall()}
        for version, desc, fn in MIGRATIONS:
            if version in done:
                continue
            for stmt in fn(cur):
                applied.append(f"v{version}: {stmt}")
            cur.execute(
                "INSERT INTO schema_migrations (version, description) VALUES (%s, %s)",
                (version, desc),
            )
            logger.info("migration v%s 已应用: %s", version, desc)
    return applied


def current_version() -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations")
        return int(cur.fetchone()["v"])
