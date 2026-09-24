"""数据集上传 API（契约 §8：POST /api/datasets）。"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.api.auth import UserCtx, get_current_user
from app.core.config import settings
from app.core.logging import get_logger
from app.data_engine.profiler import profile_csv
from app.data_engine.router import choose_engine
from app.persistence.mysql import delete_dataset, get_dataset

router = APIRouter(prefix="/api/datasets", tags=["datasets"])
logger = get_logger(__name__)

# 上传体积上限 10MB（CONTRACTS2 §5）
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


def table_name_for(dataset_id: str) -> str:
    """DuckDB 视图名：与 duckdb_engine.table_for 同一实现，避免两处漂移。"""
    from app.data_engine.duckdb_engine import table_for

    return table_for(dataset_id)


@router.post("")
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    user: UserCtx = Depends(get_current_user),
) -> dict:
    """保存 CSV → 画像 → 注册 DuckDB 视图 → 登记 MySQL → 返回元数据。

    体积校验（CONTRACTS2 §5）：Content-Length 预检 + 分块读累计 ≤10MB，超限 413。
    登录必须（CONTRACTS3 §3.4）：新写入带 user_id。
    """
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="仅支持 CSV 文件")

    # Content-Length 预检：multipart 信封比文件本身略大，预留 1MB 余量
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES + 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")

    dataset_id = str(uuid.uuid4())  # 完整 uuid 作为 dataset_id
    uuid8 = dataset_id[:8]
    name = filename.rsplit(".", 1)[0]
    path = settings.upload_dir / f"{uuid8}.csv"

    # 分块读累计校验，精确限制文件本体大小
    total = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(_READ_CHUNK):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件超过 10MB 限制")
                out.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    if not total:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="文件为空")

    # 阻塞的文件画像与引擎注册放入线程池
    try:
        profile = await asyncio.to_thread(profile_csv, str(path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"CSV 解析失败: {exc}") from exc

    try:
        from app.data_engine.duckdb_engine import duckdb_engine  # 并行模块，延迟导入

        registered = await asyncio.to_thread(
            duckdb_engine.register_dataset, dataset_id, name, str(path)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"数据集注册失败: {exc}") from exc

    # register_dataset 返回 schema 列表（历史契约写 dict，做一次归一）
    if isinstance(registered, list):
        schema = registered
    elif isinstance(registered, dict):
        schema = registered.get("schema", [])
    else:
        schema = []
    engine_hint = choose_engine(profile)

    # 落库失败仅告警，不阻断上传（降级原则）
    try:
        from app.persistence.mysql import insert_dataset

        await asyncio.to_thread(
            insert_dataset,
            dataset_id,
            name,
            str(path),
            profile.rows_estimate,
            profile.size_bytes,
            schema,
            user.user_id,
        )
    except Exception as exc:
        logger.warning("数据集落库失败 dataset=%s: %s", dataset_id, exc)

    return {
        "dataset_id": dataset_id,
        "name": name,
        "table_name": table_name_for(dataset_id),
        "schema": schema,
        "rows_estimate": profile.rows_estimate,
        "size_mb": profile.size_mb,
        "engine_hint": engine_hint,
    }


@router.delete("/{dataset_id}")
async def remove_dataset(dataset_id: str, user: UserCtx = Depends(get_current_user)) -> dict:
    """删除数据集：属主校验 → 注销 DuckDB 视图 → 删文件 → 删 MySQL 登记。

    隔离（CONTRACTS3 §3.4）：user_id ∈ {NULL(公共遗留), 当前用户} 才可删，否则 404。
    清理失败不阻断删除（降级原则），只告警。
    """
    row = await asyncio.to_thread(get_dataset, dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"数据集不存在: {dataset_id}")
    if row.get("user_id") is not None and row["user_id"] != user.user_id:
        raise HTTPException(status_code=404, detail=f"数据集不存在: {dataset_id}")

    # 1) 注销 DuckDB 视图并关闭连接
    try:
        from app.data_engine.duckdb_engine import duckdb_engine

        await asyncio.to_thread(duckdb_engine.unregister_dataset, dataset_id)
    except Exception as exc:
        logger.warning("DuckDB 注销失败 dataset=%s: %s", dataset_id, exc)

    # 2) 删除上传文件
    path = row.get("path")
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("数据集文件删除失败 dataset=%s path=%s: %s", dataset_id, path, exc)

    # 3) 删除 MySQL 登记
    try:
        await asyncio.to_thread(delete_dataset, dataset_id)
    except Exception as exc:
        logger.warning("数据集落库删除失败 dataset=%s: %s", dataset_id, exc)
        raise HTTPException(status_code=500, detail="数据集删除失败") from exc

    return {"dataset_id": dataset_id, "deleted": True}
