"""简历 API：multipart 上传与 profile 查询（CONTRACTS2 §4.6）。"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.core.logging import get_logger
from app.persistence.mysql import get_resume, save_resume

router = APIRouter(prefix="/api/resumes", tags=["resumes"])
logger = get_logger(__name__)

_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_ALLOWED_EXTS = {".pdf", ".docx", ".txt"}


@router.post("")
async def upload_resume(file: UploadFile = File(...)) -> dict:
    """上传简历文件：≤10MB、后缀白名单，存 uploads/resumes/ 并登记。"""
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"仅支持 {'/'.join(sorted(_ALLOWED_EXTS))} 格式: {filename}")

    # 多读 1 字节用于判断超限
    data = await file.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")

    resume_id = uuid.uuid4().hex[:8]
    dir_path = Path(settings.upload_dir) / "resumes"
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{resume_id}{ext}"
    path.write_bytes(data)

    try:
        await asyncio.to_thread(save_resume, resume_id, filename, str(path), {})
    except Exception as exc:
        logger.warning("简历登记失败 resume=%s: %s", resume_id, exc)
        raise HTTPException(status_code=500, detail="简历登记失败") from exc
    return {"resume_id": resume_id, "filename": filename}


@router.get("/{resume_id}")
async def read_resume(resume_id: str) -> dict:
    """返回简历元信息与 profile（解析结果），无记录 404。"""
    row = await asyncio.to_thread(get_resume, resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"简历不存在: {resume_id}")
    return {
        "resume_id": row["id"],
        "filename": row["filename"],
        "profile": row["profile_json"] or {},
        "created_at": str(row.get("created_at") or ""),
    }
