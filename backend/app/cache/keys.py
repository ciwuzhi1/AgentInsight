"""缓存 key 构造（CONTRACTS3 §1.1）。"""
from __future__ import annotations

import hashlib


def text_hash(*parts: str) -> str:
    """sha256 前 16 位；parts 以 \\x1f 连接后哈希。"""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def resume_key(h: str) -> str:
    """简历画像缓存 key。"""
    return f"cache:resume:{h}"


def job_key(h: str) -> str:
    """岗位画像缓存 key。"""
    return f"cache:job:{h}"
