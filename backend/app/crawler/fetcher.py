"""HTTP 抓取：requests + 浏览器 UA（契约 §12）+ SSRF 防护。"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests

# 模拟浏览器的 UA，避免被简单反爬拦截
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


class FetchError(Exception):
    """抓取失败（网络错误 / 非 2xx / 非法目标）。"""


def _assert_public_http_url(url: str) -> None:
    """拒绝私网/环回/链路本地地址与非 http(s)，阻断 SSRF。"""
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise FetchError(f"URL 非法: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"仅允许 http/https: {parsed.scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise FetchError("URL 缺少主机名")
    if host.lower() in ("localhost", "metadata.google.internal"):
        raise FetchError(f"禁止抓取目标: {host}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"DNS 解析失败 {host}: {exc}") from exc
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str.split("%")[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise FetchError(f"禁止抓取内网/保留地址 {ip_str}（{host}）")


def fetch(url: str) -> str:
    """抓取 URL 返回文本；失败抛 FetchError。"""
    _assert_public_http_url(url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        # 跟随跳转后可能指向内网，再校验一次最终 URL
        _assert_public_http_url(resp.url)
        resp.raise_for_status()
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"抓取失败 {url}: {exc}") from exc
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text
