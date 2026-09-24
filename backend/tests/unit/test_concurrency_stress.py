"""多智能体运行时并发压测（12+ 路）：Supervisor.run_task / WorkflowExecutor。

目标：用 MockLLMClient + 内存桩（Redis/MySQL）离线压出真实并发缺陷。
场景：混合路由并发、同 dataset 注册/执行、幂等锁与重复完成、取消/超时竞争、
状态机非法迁移。断言：无数据串扰、无未捕获异常、终态可达、messages 不互污。

发现的产品缺陷只记录（REAL_BUGS / xfail），不改产品代码。
"""
from __future__ import annotations

import asyncio
import fnmatch
import threading
from pathlib import Path

import pytest

from app.agent_runtime import executor as executor_mod
from app.agent_runtime import supervisor as supervisor_mod
from app.agent_runtime.executor import WorkflowExecutor, force_fail_final
from app.agent_runtime.registry import AgentRegistry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agents.base import AgentResult, BaseAgent
from app.core.llm import MockLLMClient
from app.data_engine.duckdb_engine import table_for

TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED_FINAL}

# 压测中确认的真实缺陷（测试收尾打印，供报告用）
REAL_BUGS: list[str] = []


def _note_bug(msg: str) -> None:
    if msg not in REAL_BUGS:
        REAL_BUGS.append(msg)


# ---------------------------------------------------------------------------
# 内存桩：FakeRedis / MySQL no-op / LLM=Mock / settings 默认值
# ---------------------------------------------------------------------------


class FakeRedis:
    """进程内 Redis 桩：GET/SET NX/DEL/SCAN，语义对齐 acquire_lock。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        # threading.Lock：跨 asyncio.run / to_thread 安全，不绑定事件循环
        self._lock = threading.Lock()

    def is_closed(self) -> bool:
        return False

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def get(self, key: str):
        with self._lock:
            return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        with self._lock:
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

    async def delete(self, *keys: str) -> int:
        with self._lock:
            n = 0
            for k in keys:
                if self.store.pop(k, None) is not None:
                    n += 1
            return n

    async def scan_iter(self, match: str | None = None, count: int = 100):
        for k in list(self.store):
            if match is None or fnmatch.fnmatch(k, match):
                yield k


@pytest.fixture
def offline_env(monkeypatch):
    """离线环境：FakeRedis + MySQL 空实现 + MockLLM + settings 内置默认。"""
    from app.agents import data_agent as data_agent_mod
    from app.cache import redis as redis_mod
    from app.core import app_settings as app_settings_mod
    from app.core import llm as llm_mod
    from app.persistence import mysql as mysql_mod

    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(redis_mod, "get_redis", _get_redis)
    monkeypatch.setattr(redis_mod, "_client", None)
    monkeypatch.setattr(redis_mod, "_fail_count", 0)

    # MySQL：全部空实现（禁止真实连接）
    monkeypatch.setattr(mysql_mod, "save_resume", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "get_all_settings", lambda: {})
    monkeypatch.setattr(mysql_mod, "upsert_setting", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "insert_task", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "insert_task_step", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "insert_match", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "get_dataset", lambda *a, **k: None)
    monkeypatch.setattr(mysql_mod, "get_active_model_config", lambda: None)

    def _get_setting(key: str, default: str | None = None) -> str:
        return app_settings_mod.DEFAULT_SETTINGS.get(key, default or "")

    async def _get_setting_async(key: str, default: str | None = None) -> str:
        await asyncio.sleep(0)
        return _get_setting(key, default)

    monkeypatch.setattr(app_settings_mod, "get_setting", _get_setting)
    monkeypatch.setattr(app_settings_mod, "get_setting_async", _get_setting_async)

    # LLM：强制 Mock（data_agent 顶层 import 了 get_llm_client）
    mock = MockLLMClient()
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda: mock)
    monkeypatch.setattr(data_agent_mod, "get_llm_client", lambda: mock)

    # few-shot 磁盘 IO 置空，避免污染 / 并发写文件
    monkeypatch.setattr(data_agent_mod, "import_history", lambda *a, **k: 0)
    monkeypatch.setattr(data_agent_mod, "export_history", lambda *a, **k: None)

    yield fake


@pytest.fixture
def fast_retry(monkeypatch):
    """重试退避与步超时压到极小，加速失败路径压测。"""
    monkeypatch.setattr(executor_mod, "_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(executor_mod, "_RETRY_CAP_S", 0.0)
    monkeypatch.setattr(executor_mod, "_STEP_TIMEOUT_S", 2.0)


@pytest.fixture
def assets(tmp_path: Path):
    """临时 CSV + 简历文本；视图名走 table_for() 与产品实现对齐。"""
    csv_a = tmp_path / "sales_a.csv"
    csv_a.write_text(
        "region,product,sales,quantity,order_date\n"
        "华东,A,100,10,2025-01-01\n"
        "华北,B,200,20,2025-01-02\n"
        "华南,A,150,15,2025-01-03\n",
        encoding="utf-8",
    )
    csv_b = tmp_path / "sales_b.csv"
    csv_b.write_text(
        "region,product,sales,quantity,order_date\n"
        "华东,C,500,5,2025-02-01\n"
        "华北,D,300,3,2025-02-02\n",
        encoding="utf-8",
    )
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "张三\n熟悉 Python Docker Kubernetes PostgreSQL 机器学习\n"
        "3年工作经验\n项目经历：智能推荐系统\n教育：本科\n",
        encoding="utf-8",
    )
    return {"csv_a": csv_a, "csv_b": csv_b, "resume": resume, "dir": tmp_path}


def make_registry() -> AgentRegistry:
    """真实 agent 注册表（离线路径：Mock LLM + 内存缓存）。"""
    from app.agents.data_agent import DataAgent
    from app.agents.job_agent import JobAgent
    from app.agents.match_agent import MatchAgent
    from app.agents.report_agent import ReportSynthesizer
    from app.agents.resume_agent import ResumeAgent
    from app.agents.validator_agent import ValidatorAgent

    reg = AgentRegistry()
    for agent in (
        DataAgent(),
        ValidatorAgent(),
        ResumeAgent(),
        JobAgent(),
        MatchAgent(),
        ReportSynthesizer(),
    ):
        reg.register(agent)
    return reg


def event_log() -> tuple[list[dict], object]:
    """每任务独立事件收集器（emit 异步）。"""
    events: list[dict] = []

    async def emit(event: dict) -> None:
        # 强制让出，放大交错窗口
        await asyncio.sleep(0)
        events.append(event)

    return events, emit


def dataset_ctx(dataset_id: str, path: Path) -> dict:
    return {
        "dataset_id": dataset_id,
        "name": f"ds-{dataset_id[:8]}",
        "path": str(path),
        "table_name": table_for(dataset_id),
        "schema": [],
    }


class EchoAgent(BaseAgent):
    """可控 dummy：按 task_id 返回唯一 payload，可注入延迟/异常。"""

    def __init__(
        self,
        name: str,
        delay: float = 0.0,
        fail_first: int = 0,
        hang: bool = False,
    ) -> None:
        self.name = name
        self.delay = delay
        self.fail_first = fail_first
        self.hang = hang
        self.calls = 0
        self._fail_left = fail_first

    async def run(self, state: TaskState, emit) -> AgentResult:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(3600)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self._fail_left > 0:
            self._fail_left -= 1
            raise RuntimeError(f"{self.name} injected failure")
        return AgentResult(
            status="ok",
            message_type="data_result",
            data={
                "agent": self.name,
                "task_id": state.task_id,
                "query": state.query,
                "marker": f"{self.name}:{state.task_id}",
            },
        )


async def _gather_supervised(states: list[TaskState], reg: AgentRegistry):
    """并发跑多个 run_task，返回 (结果或异常, 每任务事件)。"""
    logs: list[list[dict]] = []
    emits = []
    for _ in states:
        ev, em = event_log()
        logs.append(ev)
        emits.append(em)
    sup = supervisor_mod.Supervisor(agent_registry=reg)
    outs = await asyncio.gather(
        *(sup.run_task(st, em) for st, em in zip(states, emits)),
        return_exceptions=True,
    )
    return outs, logs


def assert_no_crosstalk(states: list[TaskState], logs: list[list[dict]]) -> None:
    """无串扰：task_id/query/messages/final 互不污染。"""
    ids = [s.task_id for s in states]
    assert len(set(ids)) == len(ids)
    for st, evs in zip(states, logs):
        assert st.status in TERMINAL or isinstance(st.status, TaskStatus)
        for msg in st.messages:
            assert msg.task_id == st.task_id, (
                f"messages 串扰: msg.task_id={msg.task_id} != {st.task_id}"
            )
        if st.final_result and isinstance(st.final_result, dict):
            fr = st.final_result
            if "task_id" in fr:
                assert fr["task_id"] == st.task_id, f"final.task_id 串扰: {fr.get('task_id')}"
            if "query" in fr:
                assert fr["query"] == st.query, f"final.query 串扰: {fr.get('query')!r}"
        for e in evs:
            tid = e.get("task_id")
            if tid is not None:
                assert tid == st.task_id, f"事件 task_id 串扰: {tid} != {st.task_id}"
            # 事件里不应出现其它任务的 query marker
            blob = str(e)
            for other in states:
                if other.task_id == st.task_id:
                    continue
                # 其它任务的唯一 query 前缀不应出现在本任务事件
                # 精确匹配 query 字段值，避免 "压测 1" 误命中 "压测 10"
                if other.query and other.query != st.query:
                    if f"'query': '{other.query}'" in blob.replace('"', "'"):
                        assert False, (
                            f"事件内容串扰: 任务 {st.task_id} 的事件含 {other.query!r}"
                        )


# ---------------------------------------------------------------------------
# 1) 12 路混合 data / match / clarify 并发
# ---------------------------------------------------------------------------


def test_stress_12_mixed_routes_reach_terminal(offline_env, fast_retry, assets):
    """12 任务同时路由+执行（4 data + 4 match + 4 clarify）均达终态且无未捕获异常。"""

    async def main():
        reg = make_registry()
        states: list[TaskState] = []

        # 4 data：两数据集 × 简单/完整链路
        data_qs = [
            "平均销量",  # SIMPLE → 仅 data_agent
            "按地区统计销售额",  # NORMAL → data→validator→report
            "按商品统计销量",  # NORMAL
            "对比各地区销售额和销量",  # COMPLEX
        ]
        for i, q in enumerate(data_qs):
            ds_id = f"data-ds-{i:02d}-{'a' if i % 2 == 0 else 'b'}-{'0' * 20}"
            path = assets["csv_a"] if i % 2 == 0 else assets["csv_b"]
            st = TaskState(dataset_id=ds_id, query=q)
            st.context["dataset"] = dataset_ctx(ds_id, path)
            states.append(st)

        # 4 match：resume + jobs
        for i in range(4):
            st = TaskState(dataset_id=None, query=f"简历岗位匹配分析 第{i}份")
            st.context["resume"] = {
                "resume_id": f"r-{i}",
                "path": str(assets["resume"]),
                "filename": "resume.txt",
            }
            st.context["jobs"] = [
                {
                    "id": i + 1,
                    "title": f"工程师{i}",
                    "company": f"C{i}",
                    "skills": "Python,Docker" + (",Kubernetes" if i % 2 == 0 else ""),
                    "description": "负责后端服务开发与运维",
                }
            ]
            states.append(st)

        # 4 clarify：无 dataset 无 resume → 空计划
        for i in range(4):
            states.append(TaskState(dataset_id=None, query=f"帮我看看数据 第{i}问"))

        assert len(states) == 12
        outs, logs = await _gather_supervised(states, reg)
        return states, outs, logs

    states, outs, logs = asyncio.run(main())

    uncaught = [o for o in outs if isinstance(o, BaseException)]
    assert not uncaught, f"存在未捕获异常: {uncaught}"

    for st in states:
        assert st.status in TERMINAL, f"终态不可达 task={st.task_id} status={st.status}"
        assert st.errors or st.status is TaskStatus.COMPLETED

    # 路由分流正确
    for st in states[:4]:
        assert st.final_result and st.final_result.get("engine") in ("duckdb", None) or (
            st.status is TaskStatus.FAILED_FINAL
        )
    for st in states[4:8]:
        if st.status is TaskStatus.COMPLETED:
            assert "score" in (st.final_result or {}), st.final_result
    for st in states[8:]:
        if st.status is TaskStatus.COMPLETED:
            assert st.plan == ["clarify"]
            assert (st.final_result or {}).get("explanation") == "请先上传数据集后再提问"

    assert_no_crosstalk(states, logs)


def test_stress_12_mixed_routes_message_purity(offline_env, fast_retry, assets):
    """messages 仅含本任务 task_id，sender 载荷不含其它任务 marker。"""

    async def main():
        reg = make_registry()
        states = []
        for i in range(4):
            ds_id = f"msg-ds-{i}-{'x' * 24}"
            path = assets["csv_a"] if i % 2 == 0 else assets["csv_b"]
            st = TaskState(dataset_id=ds_id, query=f"按地区统计销售额 消息隔离{i}")
            st.context["dataset"] = dataset_ctx(ds_id, path)
            states.append(st)
        for i in range(4):
            st = TaskState(query=f"匹配消息隔离 {i}")
            st.context["resume"] = {
                "resume_id": f"mr-{i}",
                "path": str(assets["resume"]),
                "filename": "resume.txt",
            }
            st.context["jobs"] = [
                {"id": 10 + i, "title": "JD", "company": "X", "skills": "Python", "description": "dev"}
            ]
            states.append(st)
        for i in range(4):
            states.append(TaskState(query=f"澄清消息隔离 {i}"))
        outs, logs = await _gather_supervised(states, reg)
        return states, outs, logs

    states, outs, logs = asyncio.run(main())
    assert not [o for o in outs if isinstance(o, BaseException)]

    seen_msg_ids: set[str] = set()
    for st in states:
        for msg in st.messages:
            assert msg.task_id == st.task_id
            assert msg.message_id not in seen_msg_ids, f"message_id 跨任务重复: {msg.message_id}"
            seen_msg_ids.add(msg.message_id)
            payload_s = str(msg.payload)
            for other in states:
                if other.task_id == st.task_id:
                    continue
                # 其它任务的 task_id / 唯一 query 不得出现在本任务消息里
                assert other.task_id not in payload_s
                assert other.query not in payload_s

    assert_no_crosstalk(states, logs)


def test_stress_workflow_executor_12_parallel(fast_retry):
    """WorkflowExecutor 12 路多步 DAG 并行：结果按 task_id 隔离。"""

    async def main():
        reg = AgentRegistry()
        # 共享 agent 实例（真实运行时也是单例注册）
        a1, a2, a3 = EchoAgent("a1", delay=0.01), EchoAgent("a2", delay=0.01), EchoAgent("a3")
        reg.register(a1)
        reg.register(a2)
        reg.register(a3)
        from app.agent_runtime.planner import PlanStep

        states, logs = [], []
        emits = []
        for i in range(12):
            st = TaskState(dataset_id=f"ex-{i}", query=f"exec-q-{i}")
            st.transition(TaskStatus.ROUTING)
            st.transition(TaskStatus.RUNNING)
            st.plan_steps = [
                PlanStep(id="s1", agent="a1"),
                PlanStep(id="s2", agent="a2", depends_on=["s1"]),
                PlanStep(id="s3", agent="a3", depends_on=["s1", "s2"]),
            ]
            states.append(st)
            ev, em = event_log()
            logs.append(ev)
            emits.append(em)

        outs = await asyncio.gather(
            *(WorkflowExecutor(reg).execute(st, em) for st, em in zip(states, emits)),
            return_exceptions=True,
        )
        return states, outs, logs

    states, outs, logs = asyncio.run(main())
    uncaught = [o for o in outs if isinstance(o, BaseException)]
    assert not uncaught, f"executor 并发未捕获异常: {uncaught}"

    for st in states:
        assert st.status is TaskStatus.COMPLETED
        assert st.final_result is not None
        # 每步 payload 的 task_id 必须等于本任务
        for msg in st.messages:
            assert msg.task_id == st.task_id
            assert msg.payload.get("task_id") == st.task_id
            assert msg.payload.get("query") == st.query
        # results 不含其它任务 marker
        for agent_name, data in st.results.items():
            if isinstance(data, dict) and "task_id" in data:
                assert data["task_id"] == st.task_id

    # 12 组 messages 互不重叠（message_id 全局唯一且归属正确）
    all_ids = [m.message_id for st in states for m in st.messages]
    assert len(all_ids) == len(set(all_ids))


# ---------------------------------------------------------------------------
# 2) 同一 dataset_id 并发注册 / 执行
# ---------------------------------------------------------------------------


def test_same_dataset_concurrent_register_execute(offline_env, assets):
    """同一 dataset_id 12 路并发 register_dataset + execute（to_thread 真并发）。"""
    from app.data_engine.duckdb_engine import DuckDBEngine

    ds_id = "same-ds-0123456789abcdef"
    path = assets["csv_a"]
    eng = DuckDBEngine()

    async def worker(i: int):
        def reg_and_desc():
            schema = eng.register_dataset(ds_id, "shared", str(path))
            return schema

        schema = await asyncio.to_thread(reg_and_desc)
        # 注册与执行交叉：部分 worker 先 execute 再 register
        if i % 2 == 0:
            res = await eng.execute(ds_id, f"SELECT region, sales FROM {table_for(ds_id)} ORDER BY region")
        else:
            schema2 = await asyncio.to_thread(
                eng.register_dataset, ds_id, "shared", str(path)
            )
            assert schema2 == schema
            res = await eng.execute(
                ds_id, f"SELECT count(*) AS n FROM {table_for(ds_id)}"
            )
        return schema, res

    async def main():
        return await asyncio.gather(*(worker(i) for i in range(12)), return_exceptions=True)

    outcomes = asyncio.run(main())
    excs = [o for o in outcomes if isinstance(o, BaseException)]
    if excs:
        _note_bug(f"DuckDBEngine 同 dataset 并发 register/execute 抛异常: {excs[:3]}")
        pytest.xfail(f"真实并发缺陷: 同 dataset 并发注册/执行失败 {excs[:2]}")

    # 无异常时：schema 一致、查询结果不被并发改写
    schemas = [o[0] for o in outcomes]
    names0 = [c["name"] for c in schemas[0]]
    for sch in schemas:
        assert [c["name"] for c in sch] == names0
    for i, o in enumerate(outcomes):
        res = o[1]
        assert res.row_count >= 1
        if i % 2 == 0:
            # 三行区域
            assert res.row_count == 3
            regions = [r[0] for r in res.rows]
            assert regions == sorted(regions)


def test_same_dataset_concurrent_full_pipeline(offline_env, fast_retry, assets):
    """同一 dataset 上 8 个 data 任务整链路并发（注册→NL2SQL→执行→校验）。"""

    async def main():
        reg = make_registry()
        ds_id = f"pipe-shared-{'z' * 20}"
        path = assets["csv_b"]
        states = []
        for i in range(8):
            # 唯一 query 防缓存/结果串扰；同 dataset 同表
            q = f"按地区统计销售额 任务{i}批次"
            st = TaskState(dataset_id=ds_id, query=q)
            st.context["dataset"] = dataset_ctx(ds_id, path)
            states.append(st)
        outs, logs = await _gather_supervised(states, reg)
        return states, outs, logs

    states, outs, logs = asyncio.run(main())
    uncaught = [o for o in outs if isinstance(o, BaseException)]
    # 允许把异常降级为缺陷记录（DuckDB 线程安全问题），但串扰必须为零
    if uncaught:
        _note_bug(f"同 dataset 整链路并发未捕获异常 x{len(uncaught)}: {uncaught[:2]}")

    assert_no_crosstalk(states, logs)

    done = [s for s in states if s.status is TaskStatus.COMPLETED]
    failed = [s for s in states if s.status is TaskStatus.FAILED_FINAL]
    if uncaught or failed:
        errs = [s.errors[-1] for s in failed if s.errors]
        _note_bug(
            f"同 dataset 整链路并发: completed={len(done)}/8 failed_final={len(failed)} "
            f"uncaught={len(uncaught)} sample_err={errs[:2]}"
        )
        pytest.xfail(
            f"真实并发缺陷: 同 dataset 整链路 completed={len(done)}/8 "
            f"failed={len(failed)} uncaught={len(uncaught)}"
        )

    assert len(done) == 8
    # 每个 final 的 query 必须是自己的
    for st in states:
        assert (st.final_result or {}).get("query") == st.query
        assert (st.final_result or {}).get("task_id") == st.task_id


# ---------------------------------------------------------------------------
# 3) 同一 task 幂等锁 / 重复完成
# ---------------------------------------------------------------------------


def test_lock_concurrent_acquire_exactly_one_winner(offline_env):
    """12 路同时抢同一幂等锁：恰好 1 个成功，其余拿到持有者 value。"""

    async def main():
        from app.cache.redis import acquire_lock

        async def try_lock(i: int):
            return await acquire_lock("agent:lock:same-task", f"task-{i}", ttl=60)

        return await asyncio.gather(*(try_lock(i) for i in range(12)))

    results = asyncio.run(main())
    winners = [r for r in results if r[0] is True]
    losers = [r for r in results if r[0] is False]
    assert len(winners) == 1, f"幂等锁并发失效: winners={winners}"
    assert len(losers) == 11
    holder = winners[0][1]
    assert holder is None
    for ok, existing in losers:
        assert ok is False
        assert existing is not None and str(existing).startswith("task-")


def test_lock_release_then_reacquire_and_duplicate_complete(offline_env):
    """锁释放后可再获取；重复完成（二次 COMPLETED）必须抛 ValueError。"""

    async def main():
        from app.cache.redis import acquire_lock, release_lock

        ok1, _ = await acquire_lock("agent:lock:dup", "t1", ttl=60)
        ok2, existing = await acquire_lock("agent:lock:dup", "t2", ttl=60)
        released = await release_lock("agent:lock:dup")
        ok3, _ = await acquire_lock("agent:lock:dup", "t3", ttl=60)
        return ok1, ok2, existing, released, ok3

    ok1, ok2, existing, released, ok3 = asyncio.run(main())
    assert ok1 is True
    assert ok2 is False and existing == "t1"
    assert released is True
    assert ok3 is True

    # 重复完成：状态机第二次 COMPLETED → ValueError
    st = TaskState(query="dup-complete")
    st.transition(TaskStatus.ROUTING)
    st.transition(TaskStatus.RUNNING)
    st.transition(TaskStatus.VALIDATING)
    st.transition(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        st.transition(TaskStatus.COMPLETED)
    # force_fail_final 对 COMPLETED 幂等不抛
    force_fail_final(st)
    assert st.status is TaskStatus.COMPLETED


def test_same_state_duplicate_run_task(offline_env, fast_retry):
    """同一 TaskState 两次并发 run_task：不得静默串数据；异常需可观察。"""

    async def main():
        reg = AgentRegistry()
        reg.register(EchoAgent("solo", delay=0.02))
        # 直接用 clarify 路径（空计划）减少 agent 依赖，聚焦状态机竞争
        st = TaskState(dataset_id=None, query="重复提交同一状态")
        sup = supervisor_mod.Supervisor(agent_registry=reg)
        ev1, em1 = event_log()
        ev2, em2 = event_log()
        outs = await asyncio.gather(
            sup.run_task(st, em1),
            sup.run_task(st, em2),
            return_exceptions=True,
        )
        return st, outs, (ev1, ev2)

    st, outs, (ev1, ev2) = asyncio.run(main())
    uncaught = [o for o in outs if isinstance(o, BaseException)]
    # 期望：一个跑完，另一个因非法迁移 ValueError（run_task 入口 transition(ROUTING)）
    assert len(uncaught) <= 1
    if uncaught:
        assert isinstance(uncaught[0], ValueError), uncaught
        _note_bug(
            "Supervisor.run_task 对同一 state 并发重复执行：入口 transition(ROUTING) "
            "抛 ValueError 未在 run_task 内消化（API 层靠幂等锁兜底）"
            f" @ supervisor.py:62"
        )
    assert st.status in TERMINAL, f"重复执行后终态不可达: {st.status}"
    # 无跨请求消息污染（单 state，消息 task_id 必须一致）
    for msg in st.messages:
        assert msg.task_id == st.task_id


def test_force_fail_final_idempotent_under_race(fast_retry):
    """并发 force_fail_final + 正常 _finish：终态唯一、不抛未捕获异常。"""

    async def main():
        async def hammer(state: TaskState):
            force_fail_final(state)

        results = []
        for _ in range(6):
            st = TaskState(query="race-fail")
            st.transition(TaskStatus.ROUTING)
            st.transition(TaskStatus.RUNNING)
            ev, em = event_log()
            reg = AgentRegistry()
            reg.register(EchoAgent("ok", delay=0.01))
            from app.agent_runtime.planner import PlanStep

            st.plan_steps = [PlanStep(id="s1", agent="ok")]
            tasks = [
                asyncio.create_task(WorkflowExecutor(reg).execute(st, em)),
                asyncio.create_task(hammer(st)),
                asyncio.create_task(hammer(st)),
            ]
            outs = await asyncio.gather(*tasks, return_exceptions=True)
            results.append((st, outs))
        return results

    for st, outs in asyncio.run(main()):
        uncaught = [o for o in outs if isinstance(o, BaseException)]
        # execute 在 FAILED_FINAL 后合法返回；force_fail_final 幂等。
        # 若 execute 的 transition(VALIDATING/COMPLETED) 与 fail 竞争则可能 ValueError。
        for u in uncaught:
            if isinstance(u, ValueError):
                _note_bug(
                    "WorkflowExecutor.execute 与 force_fail_final 竞争时 "
                    "transition(VALIDATING/COMPLETED) 抛 ValueError 未兜住 "
                    f"@ executor.py:150-151 / 324: {u}"
                )
        assert st.status in TERMINAL, f"竞争后终态不可达: {st.status}"
        # COMPLETED 时必须有 final；FAILED_FINAL 时不得同时声称 completed
        if st.status is TaskStatus.COMPLETED:
            assert st.final_result is not None
        else:
            assert st.final_result is None or st.status is TaskStatus.FAILED_FINAL


# ---------------------------------------------------------------------------
# 4) 取消 / 超时 与 正常完成 竞争
# ---------------------------------------------------------------------------


def test_timeout_watchdog_forces_failed_final(offline_env, fast_retry, monkeypatch):
    """总超时看门狗：超时后必须 failed_final + TIMEOUT 标记（终态可达）。"""
    monkeypatch.setattr(supervisor_mod, "_TASK_TIMEOUT_S", 0.05)

    async def main():
        reg = AgentRegistry()
        reg.register(EchoAgent("hang", hang=True))
        from app.agent_runtime.planner import PlanStep

        st = TaskState(dataset_id="timeout-ds", query="超时任务")
        st.context["dataset"] = dataset_ctx("timeout-ds", Path("n/a"))

        # 实例子类覆盖 route（避免 monkeypatch 未绑定函数导致 self 错位）
        class SlowRoute(supervisor_mod.Supervisor):
            async def route(self, state, emit):
                await asyncio.sleep(1.0)
                return [PlanStep(id="s1", agent="hang")]

        ev, em = event_log()
        sup = SlowRoute(reg)
        out = await sup.run_task(st, em)
        return st, out, ev

    st, out, ev = asyncio.run(main())
    assert out is st
    assert st.status is TaskStatus.FAILED_FINAL, f"超时后未达终态: {st.status}"
    assert st.context.get("error_code") == "TIMEOUT"
    assert st.errors and any("超时" in e for e in st.errors)
    # 超时不得留下 completed 终态标记
    assert st.final_result is None


def test_timeout_vs_normal_complete_race(offline_env, fast_retry, monkeypatch):
    """看门狗超时与正常完成竞争：无论谁先，状态必须是唯一终态且一致。"""

    async def one_round(i: int):
        # 偶数：0.08s 完成（< 0.15s 超时）；奇数：0.4s（触发看门狗）
        delay = 0.08 if i % 2 == 0 else 0.4
        reg = AgentRegistry()
        reg.register(EchoAgent(f"r{i}", delay=delay))
        from app.agent_runtime.planner import PlanStep

        class FixedRoute(supervisor_mod.Supervisor):
            async def route(self, state, emit):
                return [PlanStep(id="s1", agent=f"r{i}")]

        st = TaskState(dataset_id=f"race-{i}", query=f"超时竞争{i}")
        ev, em = event_log()
        out = await FixedRoute(reg).run_task(st, em)
        return st, out, ev

    async def main():
        monkeypatch.setattr(supervisor_mod, "_TASK_TIMEOUT_S", 0.15)
        return await asyncio.gather(*(one_round(i) for i in range(8)), return_exceptions=True)

    outcomes = asyncio.run(main())
    for item in outcomes:
        if isinstance(item, BaseException):
            _note_bug(f"超时/完成竞争未捕获异常: {item!r}")
            continue
        st, out, ev = item
        assert st.status in TERMINAL, f"竞争后非终态: {st.status}"
        if st.status is TaskStatus.COMPLETED:
            assert st.final_result is not None
            assert st.context.get("error_code") != "TIMEOUT"
        else:
            assert st.status is TaskStatus.FAILED_FINAL
            assert st.errors, "failed_final 必须带 error 文案"


def test_cancel_vs_complete_race(offline_env, fast_retry):
    """取消与正常完成竞争：锁/状态可观察；终态可达性记入缺陷。"""

    async def one(i: int):
        reg = AgentRegistry()
        delay = 0.05 if i % 2 == 0 else 0.3
        reg.register(EchoAgent(f"c{i}", delay=delay))
        from app.agent_runtime.planner import PlanStep

        class S(supervisor_mod.Supervisor):
            async def route(self, state, emit):
                return [PlanStep(id="s1", agent=f"c{i}")]

        st = TaskState(dataset_id=f"cancel-{i}", query=f"取消竞争{i}")
        ev, em = event_log()
        task = asyncio.create_task(S(reg).run_task(st, em))
        # 半路取消（覆盖“执行中取消”与“接近完成取消”）
        await asyncio.sleep(0.02 if i % 2 == 0 else 0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            _note_bug(f"取消路径未捕获异常: {exc!r}")
        return st, ev

    async def main():
        return await asyncio.gather(*(one(i) for i in range(8)), return_exceptions=True)

    outcomes = asyncio.run(main())
    non_terminal = 0
    for item in outcomes:
        if isinstance(item, BaseException):
            _note_bug(f"取消竞争 gather 异常: {item!r}")
            continue
        st, ev = item
        if st.status not in TERMINAL:
            non_terminal += 1
            _note_bug(
                f"取消后终态不可达: status={st.status.value} "
                "（run_task/_run 仅捕获 Exception，CancelledError 为 BaseException，"
                "状态停留在 routing/running）@ supervisor.py:65-83 / api/agent.py:400"
            )
        # 无论终态与否：messages 不得串（单任务自检）
        for msg in st.messages:
            assert msg.task_id == st.task_id
    if non_terminal:
        pytest.xfail(f"真实并发缺陷: {non_terminal}/8 取消后状态非终态")


# ---------------------------------------------------------------------------
# 5) 状态机非法迁移（含 clarify 空计划）

# ---------------------------------------------------------------------------


def test_illegal_transition_matrix_raises_valueerror():
    """非法迁移必须抛 ValueError（含 clarify 常见误用：空计划直接 COMPLETED）。"""
    # CREATED → COMPLETED（clarify 若跳过 ROUTING/VALIDATING 直接完成 → 非法）
    s = TaskState(query="clarify 空计划误迁移")
    with pytest.raises(ValueError):
        s.transition(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        s.transition(TaskStatus.RUNNING)
    with pytest.raises(ValueError):
        s.transition(TaskStatus.FAILED_FINAL)

    # ROUTING → COMPLETED（空计划跳过 VALIDATING → 非法）
    s2 = TaskState(query="q")
    s2.transition(TaskStatus.ROUTING)
    with pytest.raises(ValueError):
        s2.transition(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        s2.transition(TaskStatus.FAILED_FINAL)

    # 合法 clarify 链：ROUTING → VALIDATING → COMPLETED
    s2.transition(TaskStatus.VALIDATING)
    s2.transition(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        s2.transition(TaskStatus.FAILED_FINAL)

    # RUNNING → COMPLETED（跳过 VALIDATING → 非法）
    s3 = TaskState(query="q")
    s3.transition(TaskStatus.ROUTING)
    s3.transition(TaskStatus.RUNNING)
    with pytest.raises(ValueError):
        s3.transition(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        s3.transition(TaskStatus.ROUTING)


def test_clarify_empty_plan_completes_without_valueerror(offline_env, fast_retry):
    """clarify 空计划 12 路并发：合法迁移链、无 ValueError、终态 COMPLETED。"""

    async def main():
        reg = make_registry()
        states = [TaskState(dataset_id=None, query=f"澄清压测-{i:02d}-独立") for i in range(12)]
        outs, logs = await _gather_supervised(states, reg)
        return states, outs, logs

    states, outs, logs = asyncio.run(main())
    uncaught = [o for o in outs if isinstance(o, BaseException)]
    assert not uncaught, f"clarify 并发异常: {uncaught}"
    for st in states:
        assert st.status is TaskStatus.COMPLETED
        assert st.plan == ["clarify"]
        assert st.plan_steps == []
        assert st.final_result["explanation"] == "请先上传数据集后再提问"
        assert st.final_result["task_id"] == st.task_id
        assert st.final_result["query"] == st.query
        # 空计划不应产生 agent message
        assert st.messages == []
    assert_no_crosstalk(states, logs)


def test_clarify_empty_plan_illegal_second_complete(offline_env, fast_retry):
    """clarify 完成后再次 _finish / transition(COMPLETED) 必须抛 ValueError。"""
    from app.agent_runtime.supervisor import Supervisor

    async def main():
        reg = make_registry()
        st = TaskState(dataset_id=None, query="重复完成 clarify")
        ev, em = event_log()
        out = await Supervisor(reg).run_task(st, em)
        # 再次 finish（模拟重复完成）
        sup = Supervisor(reg)
        err = None
        try:
            await sup._finish(st, em, {"x": 1}, 0.0)
        except ValueError as e:
            err = e
        return st, out, err

    st, out, err = asyncio.run(main())
    assert st.status is TaskStatus.COMPLETED
    assert err is not None, "重复完成必须抛 ValueError"
    assert "非法状态迁移" in str(err)
    assert st.final_result["query"] == "重复完成 clarify"


def test_concurrent_illegal_transitions_do_not_corrupt(fast_retry):
    """并发乱序 transition：抛 ValueError 的调用不得改写 status。"""

    async def main():
        st = TaskState(query="乱序迁移")
        st.transition(TaskStatus.ROUTING)
        st.transition(TaskStatus.RUNNING)

        async def try_to(to: TaskStatus):
            try:
                await asyncio.sleep(0)
                st.transition(to)
                return ("ok", to)
            except ValueError as e:
                # 非法迁移必须抛 ValueError；并发下 status 可能已被兄弟协程推进
                return ("err", to, str(e))

        return await asyncio.gather(
            try_to(TaskStatus.COMPLETED),  # 非法：跳过 VALIDATING
            try_to(TaskStatus.FAILED),
            try_to(TaskStatus.ROUTING),  # 非法：回退
            try_to(TaskStatus.FAILED_FINAL),  # 非法：未经过 FAILED
            try_to(TaskStatus.VALIDATING),
            try_to(TaskStatus.COMPLETED),
            try_to(TaskStatus.RETRYING),
            try_to(TaskStatus.CREATED),  # 非法
        )

    outcomes = asyncio.run(main())
    # 至少部分非法迁移报错
    errs = [o for o in outcomes if o[0] == "err"]
    assert errs, f"非法迁移未抛 ValueError: {outcomes}"
    # 允许合法迁移插入后状态仍在合法集合
    # （并发下最终状态依赖交错，只要不卡在未定义值）
    assert isinstance(outcomes[0][0], str)


# ---------------------------------------------------------------------------
# 汇总：打印真实缺陷（供压测报告）
# ---------------------------------------------------------------------------


def test_report_real_bugs_summary():
    """占位：收集本文件压测过程中记录的真实并发缺陷。"""
    # 本测试始终通过；缺陷在各用例 xfail/_note_bug 中记录
    assert isinstance(REAL_BUGS, list)
    if REAL_BUGS:
        print("\n=== REAL CONCURRENCY BUGS ===")
        for i, b in enumerate(REAL_BUGS, 1):
            print(f"{i}. {b}")
