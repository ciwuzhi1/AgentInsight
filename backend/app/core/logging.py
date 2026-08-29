"""日志助手：标准 logging + 简洁 formatter，根 handler 只配一次（契约 §16）。"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def _ensure_root_configured() -> None:
    """给根 logger 挂一个 stdout handler（幂等）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    if not root.handlers:  # 已被外部（如 uvicorn）配置过则不重复挂
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s", "%H:%M:%S"
            )
        )
        root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """取带统一配置的 logger。"""
    _ensure_root_configured()
    return logging.getLogger(name)
