"""TaskBus / step 序号映射单测（CONTRACTS2 §5 / §7）：截断回放、TTL 清扫、step 映射。

全程离线：不触 DB 路由（TaskBus 与 _step_seq 为纯内存逻辑）。
"""
import asyncio

from app.api import agent as agent_mod
from app.api.agent import TaskBus, _STEP_SEQS, _step_seq


def test_event_cap_600_keeps_recent_500_with_truncated_marker():
    """灌 600 事件 → 回放头部 {"type":"truncated"} + 最近 500 条，final 保留在末尾。"""

    async def main():
        bus = TaskBus()
        tid = "task-cap"
        for i in range(599):  # 599 条 progress + 1 条 final = 600
            await bus.publish(tid, {"type": "progress", "i": i})
        await bus.publish(tid, {"type": "final", "result": {"ok": True}})

        received = []
        async for event in bus.subscribe(tid):
            received.append(event)
        return bus, received

    bus, received = asyncio.run(main())
    assert received[0] == {"type": "truncated"}
    assert len(received) == 501  # truncated 标注 + 最近 500 条
    assert received[-1]["type"] == "final"  # final 保留在末尾
    # drop-oldest：保留下的是第 101~600 条发布的事件
    assert received[1] == {"type": "progress", "i": 100}
    # 详情接口回放同样带 truncated 标注
    snapshot = bus.snapshot("task-cap")
    assert snapshot[0] == {"type": "truncated"}
    assert len(snapshot) == 501


def test_finished_task_ttl_sweep(monkeypatch):
    """完结任务 TTL 到期被清扫（events 清空、_STEP_SEQS 清理），未完结任务保留。"""

    async def main():
        bus = TaskBus()
        await bus.publish("task-done", {"type": "progress"})
        await bus.publish("task-done", {"type": "final", "result": {}})  # 终态 → finished_at 打点
        await bus.publish("task-live", {"type": "progress"})  # 未完结
        _STEP_SEQS["task-done"] = {"resume": 1}
        _STEP_SEQS["task-live"] = {"data": 1}
        bus._sweep()  # TTL 未到期：什么都不清
        alive = set(bus._tasks)
        return bus, alive

    bus, alive_after_first_sweep = asyncio.run(main())
    assert alive_after_first_sweep == {"task-done", "task-live"}

    # 缩短 TTL 到 0（注入假时钟的等价手段）→ 完结任务被清扫
    monkeypatch.setattr(agent_mod, "_FINISHED_TTL_S", 0)
    bus._sweep()
    assert not bus.exists("task-done")
    assert "task-done" not in _STEP_SEQS
    assert bus.exists("task-live")
    assert "task-live" in _STEP_SEQS


def test_step_seq_mapping_reuse():
    """step id 首次出现按顺序编号，重复 id 复用序号，任务间互不影响。"""
    _STEP_SEQS.clear()
    try:
        assert _step_seq("taskA", "resume") == 1
        assert _step_seq("taskA", "job") == 2
        assert _step_seq("taskA", "match") == 3
        assert _step_seq("taskA", "resume") == 1  # 复用已有映射
        assert _step_seq("taskA", "job") == 2

        assert _step_seq("taskB", "resume") == 1  # 新任务独立编号
        assert _STEP_SEQS["taskA"] == {"resume": 1, "job": 2, "match": 3}
        assert _STEP_SEQS["taskB"] == {"resume": 1}
    finally:
        _STEP_SEQS.clear()
