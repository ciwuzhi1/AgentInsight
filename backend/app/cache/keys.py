"""缓存 key 构造（CONTRACTS3 §1.1）。"""
from __future__ import annotations

import hashlib


def text_hash(*parts: str) -> str:
    """sha256 前 16 位；parts 以 \\x1f 连接后哈希。"""
    joined = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def bytes_hash(data: bytes) -> str:
    """直接对字节做 sha256，取前 16 位（避免 decode 中间态）。"""
    return hashlib.sha256(data).hexdigest()[:16]


def resume_key(h: str) -> str:
    """简历画像缓存 key。"""
    return f"cache:resume:{h}"


def job_key(h: str) -> str:
    """岗位画像缓存 key。"""
    return f"cache:job:{h}"


def nl2sql_key(dataset_id: str, schema_hash: str, query: str) -> str:
    """NL2SQL 结果缓存 key：同一数据集+schema+问题复用 SQL。"""
    return f"cache:nl2sql:{text_hash(dataset_id, schema_hash, query)}"
