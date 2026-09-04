"""企业级结构化日志：JSON 行日志 + request_id/task_id 上下文（contextvars）。

用法不变：`from app.core.logging import get_logger`；
上下文注入：中间件调 set_request_id()，任务执行调 set_task_id()；
业务附加字段：logger.info("msg", extra={"extra": {"key": v}})
每条日志输出单行 JSON：{"ts","level","logger","msg","request_id","task_id",...}
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime

_CONFIGURED = False
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        tid = task_id_var.get()
        if tid:
            payload["task_id"] = tid
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                payload[k] = v
        if record.exc_info:
            payload["exc"] = "".join(self.formatException(record.exc_info)).strip().splitlines()[-1]
        return json.dumps(payload, ensure_ascii=False)


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def get_request_id() -> str:
    return request_id_var.get()


def set_task_id(value: str) -> None:
    task_id_var.set(value)


def _ensure_root_configured() -> None:
    """给根 logger 挂一个 stdout JSON handler（幂等）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    if not root.handlers:  # 已被外部（如 uvicorn）配置过则不重复挂
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """取带统一配置的 logger。"""
    _ensure_root_configured()
    return logging.getLogger(name)
