"""评测体系单测（CONTRACTS3 §2.5）：用例数量/去重、routing 三类判定、
error graceful、metrics 计算、match 分数区间、nl2sql mock 链路。
全部不依赖 MySQL/Docker/网络（LLM 用 MockLLMClient monkeypatch）。"""
import asyncio

import pytest

from app.core.llm import MockLLMClient
from app.evaluation.cases import load_cases
from app.evaluation import metrics
from app.evaluation import runner as eval_runner


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_load_cases_count_and_unique(cases):
    """100 case、case_id 唯一、四类数量 60/20/10/10。"""
    assert len(cases) == 100
    ids = [c.case_id for c in cases]
    assert len(set(ids)) == 100
    counts = {k: sum(1 for c in cases if c.kind == k)
              for k in ("nl2sql", "match", "routing", "error")}
    assert counts == {"nl2sql": 60, "match": 20, "routing": 10, "error": 10}


def test_routing_three_classes(cases):
    """routing 三类判定：data → data_agent、resume → match_agent、无上下文 → clarify。"""
    sup = eval_runner.Supervisor()

    async def _emit(_: dict) -> None:
        return None

    routing = [c for c in cases if c.kind == "routing"]
    assert len(routing) == 10
    for case in routing:
        payload = case.payload
        state = eval_runner.TaskState(
            dataset_id=payload["context"].get("dataset_id"), query=payload["query"]
        )
        if payload["context"].get("resume"):
            state.context["resume"] = payload["context"]["resume"]
        steps = asyncio.run(sup.route(state, _emit))
        agents = [s.agent for s in steps]
        expected = payload["expect_agent"]
        if expected is None:
            assert not agents, case.case_id
        else:
            assert expected in agents, case.case_id


def test_error_case_graceful(cases):
    """error case graceful：不存在数据集 → failed_final 且 errors 非空；
    破坏性提问（mock 兜底 SELECT）→ completed。全程不崩。

    error_006（空 query 无上下文 clarify）走 ROUTING→VALIDATING→COMPLETED
    合法链路，现可断言 completed 且无 error。
    """
    sup = eval_runner.Supervisor(eval_runner.build_registry())
    by_id = {c.case_id: c for c in cases if c.kind == "error"}

    # 不存在的数据集：进程内走 data_agent 报错路径 → failed_final + errors 非空
    row = asyncio.run(eval_runner.run_error_case(by_id["error_004"], sup, eval_runner.make_emit([])))
    assert row["ok"] and row["detail"]["graceful"]
    assert row["detail"]["status"] == "failed_final"
    assert row["detail"]["error_count"] > 0

    # 破坏性提问 + 合法数据集：mock 兜底 SELECT → completed，无 error
    row = asyncio.run(eval_runner.run_error_case(by_id["error_002"], sup, eval_runner.make_emit([])))
    assert row["ok"] and row["detail"]["status"] == "completed"

    # 空 query 无上下文 → clarify 短路 completed（覆盖 error_006，防迁移回归）
    row = asyncio.run(eval_runner.run_error_case(by_id["error_006"], sup, eval_runner.make_emit([])))
    assert row["detail"]["status"] == "completed", row["detail"]
    assert row["detail"]["graceful"], row["detail"]


def test_match_expect_ranges(cases):
    """20 条 match 的手工分数区间与缺口断言与规则打分一致。"""
    from app.agents.match_agent import compute_match

    for case in (c for c in cases if c.kind == "match"):
        scored = compute_match(case.payload["resume"], case.payload["jobs"])
        expect = case.payload["expect"]
        assert expect["score_min"] <= scored["score"] <= expect["score_max"], (
            f"{case.case_id}: score={scored['score']} not in "
            f"[{expect['score_min']}, {expect['score_max']}]"
        )
        for g in expect["gap_contains"]:
            assert g in scored["skill_gap"], f"{case.case_id}: gap 缺少 {g}"


def test_metrics_calculations():
    """metrics 四组函数对合成 rows 计算正确。"""
    sql_rows = [
        {"case_id": "a", "kind": "nl2sql", "ok": True, "latency_ms": 100,
         "detail": {"sql_exec_ok": True, "feature_pass": True, "json_valid": True}},
        {"case_id": "b", "kind": "nl2sql", "ok": False, "latency_ms": 300,
         "detail": {"sql_exec_ok": True, "feature_pass": False, "json_valid": True}},
    ]
    s = metrics.sql_metrics(sql_rows)
    assert s["sql_exec_ok_rate"] == 1.0
    assert s["sql_feature_pass_rate"] == 0.5
    assert s["json_validity"] == 1.0
    assert s["avg_latency_ms"] == 200.0
    assert s["p95_latency_ms"] == 300.0

    match_rows = [
        {"case_id": "m1", "kind": "match", "ok": True, "latency_ms": 10,
         "detail": {"score_in_range": True, "gap_ok": True}},
        {"case_id": "m2", "kind": "match", "ok": False, "latency_ms": 20,
         "detail": {"score_in_range": True, "gap_ok": False}},
    ]
    m = metrics.match_metrics(match_rows)
    assert m["score_in_range_rate"] == 1.0
    assert m["gap_accuracy_rate"] == 0.5

    routing_rows = [
        {"case_id": "r1", "kind": "routing", "ok": True, "latency_ms": 1,
         "detail": {"correct": True}},
        {"case_id": "r2", "kind": "routing", "ok": True, "latency_ms": 2,
         "detail": {"correct": True}},
    ]
    assert metrics.routing_metrics(routing_rows)["routing_accuracy"] == 1.0

    error_rows = [
        {"case_id": "e1", "kind": "error", "ok": True, "latency_ms": 1500,
         "detail": {"graceful": True}},
        {"case_id": "e2", "kind": "error", "ok": False, "latency_ms": 10,
         "detail": {"graceful": False}},
    ]
    assert metrics.error_metrics(error_rows)["graceful_rate"] == 0.5

    total = metrics.summarize(sql_rows + match_rows + routing_rows + error_rows)
    assert total["total"] == 8 and total["failed"] == 3
    assert total["ok_rate"] == 0.625


def test_nl2sql_case_with_mock(cases, monkeypatch):
    """nl2sql 链路：mock 命中句式 → 生成合法 SQL 并在真实视图上执行、特征通过。"""
    monkeypatch.setattr(eval_runner, "get_llm_client", lambda: MockLLMClient())
    eval_runner.ensure_eval_dataset()
    case = next(c for c in cases if c.case_id == "nl2sql_001")  # 按地区统计总销售额
    row = asyncio.run(eval_runner.run_nl2sql_case(case))
    assert row["ok"], row["detail"]
    assert row["detail"]["sql_exec_ok"] and row["detail"]["feature_pass"]
    sql = row["detail"]["sql"].lower()
    assert "group by region" in sql and "sum(sales)" in sql
    assert "ds_eval0001" in sql
