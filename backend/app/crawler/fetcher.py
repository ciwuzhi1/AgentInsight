"""HTTP 抓取：requests + 浏览器 UA（契约 §12）。"""
from __future__ import annotations

import requests

# 模拟浏览器的 UA，避免被简单反爬拦截
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


class FetchError(Exception):
    """抓取失败（网络错误 / 非 2xx）。"""


def fetch(url: str) -> str:
    """抓取 URL 返回文本；失败抛 FetchError。"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        raise FetchError(f"抓取失败 {url}: {exc}") from exc
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text
