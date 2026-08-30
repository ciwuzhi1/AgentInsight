"""MinerU v4 文件解析 REST 客户端：PDF → markdown 文本（CONTRACTS2 §3.5）。

流程（按 MinerU 官方文档）：POST /api/v4/file-urls/batch 拿预签名上传链接与
batch_id → PUT 上传本地文件（远程 URL 文件直接在 url 字段提交）→ 轮询
GET /api/v4/extract-results/batch/{batch_id} → 下载 full_zip_url → 解 zip 取
full.md。任一阶段失败抛 MineruError（含阶段与截断的响应摘要），调用方负责
降级兜底，本模块不重试。
"""
from __future__ import annotations

import asyncio
import io
import os
import time
import zipfile
from pathlib import Path

import httpx

# MinerU v4 REST 基址
BASE_URL = "https://mineru.net"
# 轮询间隔（秒）
_POLL_INTERVAL_S = 3.0
# 异常信息里响应/错误片段的最大长度
_SNIP_LEN = 500


class MineruError(Exception):
    """MinerU 解析失败：携带失败阶段与响应/错误摘要。"""


def _snip(text: str) -> str:
    """截断错误摘要到 _SNIP_LEN，去空白。"""
    return " ".join((text or "").split())[:_SNIP_LEN]


def _extract_full_md(zip_bytes: bytes) -> str:
    """从结果 zip 中读 full.md 文本；找不到抛 MineruError。"""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("full.md"):
                    return zf.read(name).decode("utf-8", errors="ignore")
    except zipfile.BadZipFile as exc:
        raise MineruError(f"下载阶段：结果不是合法 zip: {exc}") from exc
    raise MineruError("下载阶段：结果 zip 中未找到 full.md")


async def parse_pdf(path: str, token: str, timeout_s: int = 180) -> str:
    """提交 PDF 给 MinerU v4 解析，返回 full.md 的文本内容。

    - token 为空立即抛 MineruError("未配置 token")。
    - path 为 http(s) URL 时直接按 url 提交；否则 PUT 上传本地文件。
    - 轮询与下载整体受 timeout_s 约束，超时抛 MineruError("轮询阶段")。
    """
    token = (token or "").strip()
    if not token:
        raise MineruError("未配置 token")

    headers = {"Authorization": f"Bearer {token}"}
    filename = os.path.basename(path)
    is_remote = path.startswith(("http://", "https://"))
    deadline = time.monotonic() + max(timeout_s, 10)

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # 1) 提交批量任务：本地文件不填 url，取预签名上传链接；远程文件直接给 url
        if is_remote:
            payload: dict = {
                "enable_formula": True,
                "enable_table": True,
                "files": [{"is_ocr": True, "data_id": filename, "url": path}],
            }
        else:
            payload = {
                "enable_formula": True,
                "enable_table": True,
                "files": [{"is_ocr": True, "data_id": filename, "url": ""}],
            }
        try:
            resp = await client.post("/api/v4/file-urls/batch", json=payload, headers=headers)
        except Exception as exc:  # noqa: BLE001 - 网络/超时统一包装
            raise MineruError(f"提交阶段：请求失败: {_snip(str(exc))}") from exc
        if resp.status_code != 200:
            raise MineruError(
                f"提交阶段：HTTP {resp.status_code}: {_snip(resp.text)}"
            )
        body = resp.json()
        if body.get("code") != 0:
            raise MineruError(f"提交阶段：业务失败: {_snip(str(body))}")
        batch_id = str((body.get("data") or {}).get("batch_id") or "")
        if not batch_id:
            raise MineruError(f"提交阶段：响应缺少 batch_id: {_snip(str(body))}")

        # 2) 本地文件：PUT 到预签名上传链接（上传后自动进入解析队列）
        if not is_remote:
            files = (body.get("data") or {}).get("file_urls") or []
            upload_url = files[0] if files else ""
            if not upload_url:
                raise MineruError(f"提交阶段：响应缺少上传链接: {_snip(str(body))}")
            data = await asyncio.to_thread(Path(path).read_bytes)
            try:
                up = await client.put(upload_url, content=data)
            except Exception as exc:  # noqa: BLE001
                raise MineruError(f"上传阶段：请求失败: {_snip(str(exc))}") from exc
            if up.status_code not in (200, 201):
                raise MineruError(
                    f"上传阶段：HTTP {up.status_code}: {_snip(up.text)}"
                )

        # 3) 轮询解析结果（间隔 3s，受 timeout_s 约束）
        while True:
            if time.monotonic() > deadline:
                raise MineruError(f"轮询阶段：超时（{timeout_s}s）batch_id={batch_id}")
            try:
                poll = await client.get(
                    f"/api/v4/extract-results/batch/{batch_id}", headers=headers
                )
            except Exception as exc:  # noqa: BLE001
                raise MineruError(f"轮询阶段：请求失败: {_snip(str(exc))}") from exc
            if poll.status_code != 200:
                raise MineruError(
                    f"轮询阶段：HTTP {poll.status_code}: {_snip(poll.text)}"
                )
            poll_body = poll.json()
            if poll_body.get("code") != 0:
                raise MineruError(f"轮询阶段：业务失败: {_snip(str(poll_body))}")
            results = (poll_body.get("data") or {}).get("extract_result") or []
            states = [str(r.get("state") or "") for r in results]
            if any(s == "failed" for s in states):
                errs = [
                    str(r.get("err_msg") or r.get("error_message") or "")
                    for r in results
                    if r.get("state") == "failed"
                ]
                raise MineruError(f"解析阶段：任务失败: {_snip('; '.join(errs))}")
            if results and all(s == "done" for s in states):
                zip_url = results[0].get("full_zip_url") or ""
                break
            await asyncio.sleep(_POLL_INTERVAL_S)

        if not zip_url:
            raise MineruError("解析阶段：任务完成但缺少 full_zip_url")

        # 4) 下载结果 zip 并取 full.md
        try:
            dl = await client.get(zip_url)
        except Exception as exc:  # noqa: BLE001
            raise MineruError(f"下载阶段：请求失败: {_snip(str(exc))}") from exc
        if dl.status_code != 200:
            raise MineruError(f"下载阶段：HTTP {dl.status_code}: {_snip(dl.text)}")
        text = await asyncio.to_thread(_extract_full_md, dl.content)

    if not text.strip():
        raise MineruError("解析阶段：full.md 内容为空")
    return text
