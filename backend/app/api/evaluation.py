"""评测报告只读接口（CONTRACTS3 §2.4）：GET /api/evaluations/last。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import UserCtx, get_current_user
from app.core.logging import get_logger
from app.evaluation.runner import default_report_path

logger = get_logger(__name__)

router = APIRouter(prefix="/api/evaluations", tags=["evaluation"])


@router.get("/last")
async def last_report(user: UserCtx = Depends(get_current_user)) -> dict:
    """返回最近一次评测报告；无报告文件返回 404。"""
    path = default_report_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="暂无评测报告，请先运行 python -m app.evaluation.runner")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 报告损坏不崩，返回明确错误
        logger.warning("评测报告读取失败 %s: %s", path, exc)
        raise HTTPException(status_code=500, detail="评测报告文件损坏") from exc
    return data
