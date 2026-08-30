"""全局配置：pydantic-settings 读取仓库根 .env，全项目只 import 本模块（契约 §2）。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py 上三级即仓库根
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """环境变量映射；字段与 .env.example 一一对应，缺 key 用宽松缺省。"""

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env", extra="ignore"
    )

    # LLM（无 key 时由 llm 工厂自动降级 mock）
    LLM_PROVIDER: str = "openai"
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_TIMEOUT: int = 60  # 单次请求超时（秒）

    # MySQL
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_ROOT_PASSWORD: str = ""  # 仅 scripts/init_db.py 使用
    MYSQL_USER: str = "agent_app"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "agentinsight"

    # Redis（挂了系统自动降级）
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # Agent Runtime
    MAX_AGENT_STEPS: int = 8
    MAX_RETRY: int = 2
    SQL_MAX_ROWS: int = 1000

    # 引擎路由：行数 >= 阈值走 Spark
    SPARK_ROW_THRESHOLD: int = 100000
    SPARK_IMAGE: str = "apache/spark:3.5.1"

    # 目录（空值时由 data_dir / upload_dir 属性派生到 <repo>/data）
    DATA_DIR: str = ""
    UPLOAD_DIR: str = ""

    @property
    def data_dir(self) -> Path:
        """有效数据目录。"""
        return Path(self.DATA_DIR) if self.DATA_DIR else _REPO_ROOT / "data"

    @property
    def upload_dir(self) -> Path:
        """有效上传目录。"""
        return Path(self.UPLOAD_DIR) if self.UPLOAD_DIR else _REPO_ROOT / "data" / "uploads"


settings = Settings()

# 确保关键目录存在
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "out").mkdir(parents=True, exist_ok=True)
settings.upload_dir.mkdir(parents=True, exist_ok=True)
