"""缓存 TTL 策略（CONTRACTS3 §1.2）。"""
from __future__ import annotations

TTL_RESUME = 24 * 3600
TTL_JOB = 24 * 3600
TTL_SCHEMA = 3600

_KIND_TTL: dict[str, int] = {
    "resume": TTL_RESUME,
    "job": TTL_JOB,
    "schema": TTL_SCHEMA,
}


def get_ttl(kind: str) -> int:
    """按类别取 TTL；未知类别回退 TTL_SCHEMA。"""
    return _KIND_TTL.get(kind, TTL_SCHEMA)
