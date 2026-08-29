"""初始化本地 MySQL：建库、建专用账号、执行三表+jobs DDL。
前置：先把 .env 里的 MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD 填好。
用法：python scripts/init_db.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "backend" / "app" / "persistence" / "schema.sql"


def load_env() -> dict:
    env = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def placeholder(v: str) -> bool:
    return not v or "填" in v or "改" in v


def main() -> int:
    try:
        import pymysql
    except ImportError:
        print("缺少 PyMySQL：先 pip install PyMySQL cryptography")
        return 1

    env = load_env()
    root_pw = env.get("MYSQL_ROOT_PASSWORD", "")
    if placeholder(root_pw):
        print("请先在 .env 填好 MYSQL_ROOT_PASSWORD（以及 MYSQL_PASSWORD）")
        return 1

    db = env.get("MYSQL_DATABASE", "agentinsight")
    user = env.get("MYSQL_USER", "agent_app")
    user_pw = env.get("MYSQL_PASSWORD", "")
    if placeholder(user_pw):
        print("请先在 .env 把 MYSQL_PASSWORD 改成真实密码（应用账号不用 root）")
        return 1

    conn = pymysql.connect(
        host=env.get("MYSQL_HOST", "127.0.0.1"),
        port=int(env.get("MYSQL_PORT", "3306")),
        user="root",
        password=root_pw,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.execute(f"CREATE USER IF NOT EXISTS '{user}'@'%%' IDENTIFIED BY %s", (user_pw,))
            cur.execute(f"ALTER USER '{user}'@'%%' IDENTIFIED BY %s", (user_pw,))
            cur.execute(f"GRANT ALL PRIVILEGES ON `{db}`.* TO '{user}'@'%'")
            cur.execute(f"USE `{db}`")
            sql = SCHEMA.read_text(encoding="utf-8")
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                cur.execute(stmt)
            cur.execute("SHOW TABLES")
            tables = [r[0] for r in cur.fetchall()]
        conn.commit()
        print(f"OK 数据库 {db} 就绪，账号 {user} 建好，表：{tables}")
        return 0
    except pymysql.err.OperationalError as e:
        print(f"MySQL 连接失败（检查服务是否启动/密码是否正确）：{e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
