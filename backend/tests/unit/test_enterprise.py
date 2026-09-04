"""企业级工程化单测：限流器 / 结构化日志 / 输入校验（全离线）。"""
import asyncio
import json
import logging

import pytest

from app.core.rate_limit import RateLimiter


def test_rate_limit_blocks_after_max():
    rl = RateLimiter(max_calls=3, window_s=60)
    assert all(rl.allow("k")[0] for _ in range(3))
    ok, wait = rl.allow("k")
    assert not ok and wait > 0


def test_rate_limit_window_slides():
    rl = RateLimiter(max_calls=1, window_s=0.05)
    assert rl.allow("k")[0]
    assert not rl.allow("k")[0]
    asyncio.run(asyncio.sleep(0.06))
    assert rl.allow("k")[0]  # 窗口滑过即放行


def test_rate_limit_keys_isolated():
    rl = RateLimiter(max_calls=1, window_s=60)
    assert rl.allow("a")[0]
    assert rl.allow("b")[0]  # 不同 key 互不影响
    assert not rl.allow("a")[0]


def test_json_log_format():
    """结构化日志：单行 JSON、含 request_id 与 extra 字段。"""
    import io
    from app.core.logging import _JsonFormatter, set_request_id

    set_request_id("test-rid-123")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(_JsonFormatter())
    lg = logging.getLogger("enterprise-test")
    lg.addHandler(handler)
    lg.propagate = False
    lg.setLevel(logging.INFO)
    try:
        lg.info("hello %s", "world", extra={"extra": {"k": "v"}})
    finally:
        lg.removeHandler(handler)
        lg.propagate = True
    d = json.loads(buf.getvalue())
    assert d["msg"] == "hello world" and d["request_id"] == "test-rid-123" and d["k"] == "v"


def test_task_query_length_capped():
    """pydantic 校验：超长 query 应被拒绝（>2000 字符）。"""
    from app.api.agent import TaskCreateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskCreateRequest(dataset_id="d", query="x" * 2001)
    assert TaskCreateRequest(dataset_id="d", query="正常问题").query == "正常问题"
