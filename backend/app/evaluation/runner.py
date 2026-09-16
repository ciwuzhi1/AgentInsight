"""评测执行器：进程内直跑 100 case，不经 HTTP、不落 MySQL（CONTRACTS3 §2.3）。

- nl2sql：get_llm_client().generate_json → sql_tool.guard → duckdb_engine.execute
  （真实 demo_sales.csv，dataset_id 用 eval 前缀 eval0001，视图 ds_eval0001）
- match：构造 AgentMessage/TaskState 直调 MatchAgent.run
- routing：直调 Supervisor().route()
- error：构造 TaskState 走 Supervisor.run_task，断言终态与 error（graceful）

CLI：python -m app.evaluation.runner [--limit N] [--kind xx] [--out data/eval_report.json]
输出：终端 Markdown 表 + JSON 报告（含全部明细）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from app.agent_runtime.message import make_message
from app.agent_runtime.registry import AgentRegistry
from app.agent_runtime.state import TaskState, TaskStatus
from app.agent_runtime.supervisor import Supervisor
from app.core.config import settings
from app.core.logging import get_logger
from app.core.llm import NL2SQL_SYSTEM_PROMPT, get_llm_client
from app.data_engine.duckdb_engine import duckdb_engine
from app.evaluation.cases import Case, load_cases
from app.evaluation.metrics import (
    error_metrics,
    match_metrics,
    routing_metrics,
    sql_metrics,
    summarize,
)
from app.tools.schema_tool import format_schema_for_prompt
from app.tools.sql_tool import guard

logger = get_logger(__name__)

# 仓库根：backend/app/evaluation/runner.py → parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_CSV = REPO_ROOT / "data" / "demo" / "demo_sales.csv"
EVAL_DATASET_ID = "eval0001"   # eval 前缀专用 dataset_id，视图 ds_eval0001，不污染业务

EmitFn = Callable[[dict], Awaitable[None]]

_ensured = False


def default_report_path() -> Path:
    """默认报告路径：<repo>/data/eval_report.json（API 与 CLI 共用）。"""
    return REPO_ROOT / "data" / "eval_report.json"


def build_registry() -> AgentRegistry:
    """构建评测专用 agent 注册表（含全部 agent）。"""
    from app.agents.data_agent import DataAgent
    from app.agents.job_agent import JobAgent
    from app.agents.match_agent import MatchAgent
    from app.agents.report_agent import ReportSynthesizer
    from app.agents.resume_agent import ResumeAgent
    from app.agents.validator_agent import ValidatorAgent

    reg = AgentRegistry()
    reg.register(DataAgent())
    reg.register(ValidatorAgent())
    reg.register(ResumeAgent())
    reg.register(JobAgent())
    reg.register(MatchAgent())
    reg.register(ReportSynthesizer())
    return reg


def ensure_eval_dataset() -> None:
    """注册 demo_sales.csv 为 eval 专用视图（幂等，仅一次）。"""
    global _ensured
    if _ensured:
        return
    duckdb_engine.register_dataset(EVAL_DATASET_ID, "demo_sales", str(DEMO_CSV))
    _ensured = True


def make_emit(events: list[dict]) -> EmitFn:
    """async 事件收集器：把 agent 的 emit 事件收进列表。"""

    async def emit(event: dict) -> None:
        events.append(event)

    return emit


# ---------- 特征断言 ----------

def check_sql_features(sql: str, expect: dict) -> bool:
    """对生成的 SQL 做结构性断言（CONTRACTS3 §2.1 expect 语义）。"""
    if not expect:
        return True
    upper = (sql or "").upper()
    lower = (sql or "").lower()
    for col in expect.get("columns") or []:
        if col.lower() not in lower:
            return False
    for agg in expect.get("aggs") or []:
        if f"{agg}(" not in upper:
            return False
    for token in expect.get("contains") or []:
        if token not in sql:
            return False
    for table in expect.get("tables") or []:
        if table.lower() not in lower:
            return False
    order_by = expect.get("order_by")
    if order_by is not None and ("order by" in lower) != order_by:
        return False
    limit_max = expect.get("limit_max")
    if limit_max is not None:
        import re

        for v in re.findall(r"\bLIMIT\s+(\d+)", upper):
            if int(v) > int(limit_max):
                return False
    return True


# ---------- 各 kind 执行 ----------

async def run_nl2sql_case(case: Case) -> dict:
    """nl2sql：mock/真模型生成 SQL → guard → 真实 DuckDB 执行 → 结构断言。"""
    ensure_eval_dataset()
    t0 = time.perf_counter()
    payload = case.payload
    question: str = payload["question"]
    expect: dict = payload.get("expect") or {}
    detail: dict = {"sql_exec_ok": False, "feature_pass": False, "json_valid": False}
    ok = False
    try:
        schema = duckdb_engine.get_schema(EVAL_DATASET_ID)
        table = f"ds_{EVAL_DATASET_ID[:8]}"
        user_prompt = (
            f"表名: {table}\n"
            f"字段:\n{format_schema_for_prompt(schema)}\n"
            f"用户问题: {question}\n"
        )
        raw = await get_llm_client().generate_json(NL2SQL_SYSTEM_PROMPT, user_prompt)
        sql = str((raw or {}).get("sql") or "").strip()
        detail["json_valid"] = bool(sql)
        if not sql:
            detail["reason"] = "LLM 未返回 SQL"
        else:
            guard_ok, clean_sql, reason = guard(sql, settings.SQL_MAX_ROWS)
            if not guard_ok:
                detail["reason"] = f"guard 拒绝: {reason}"
            else:
                detail["sql"] = clean_sql
                try:
                    result = await duckdb_engine.execute(EVAL_DATASET_ID, clean_sql)
                    detail["sql_exec_ok"] = True
                    detail["row_count"] = result.row_count
                except Exception as exc:  # noqa: BLE001 - 执行失败如实记录
                    detail["reason"] = f"SQL 执行失败: {exc}"
            # 特征断言用 guard 后的 SQL（guard 拒绝时用原始 SQL，必然 fail）
            detail["feature_pass"] = check_sql_features(detail.get("sql") or sql, expect)
        ok = detail["json_valid"] and detail["sql_exec_ok"] and detail["feature_pass"]
    except Exception as exc:  # noqa: BLE001 - 单 case 异常不中断评测
        detail["reason"] = f"{type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    return {"case_id": case.case_id, "kind": case.kind, "ok": ok,
            "latency_ms": latency, "detail": detail}


async def run_match_case(case: Case, emit: EmitFn) -> dict:
    """match：构造 resume_profile / job_profile 消息直调 MatchAgent.run。"""
    from app.agents.match_agent import MatchAgent

    t0 = time.perf_counter()
    payload = case.payload
    expect = payload["expect"]
    state = TaskState(dataset_id=None, query="简历-岗位匹配评测")
    state.messages = [
        make_message(
            task_id=state.task_id,
            sender="resume_agent",
            receiver=["match_agent"],
            type="resume_profile",
            payload={
                "resume_id": f"eval_{case.case_id}",
                "filename": f"{case.case_id}.pdf",
                "profile": payload["resume"],
            },
        ),
        make_message(
            task_id=state.task_id,
            sender="job_agent",
            receiver=["match_agent"],
            type="job_profile",
            payload={"jobs": payload["jobs"]},
        ),
    ]
    detail: dict = {"score_in_range": False, "gap_ok": False}
    ok = False
    try:
        result = await MatchAgent().run(state, emit)
        if result.status != "ok":
            detail["reason"] = "; ".join(result.errors) or "match_agent 返回 error"
        else:
            score = result.data.get("score")
            detail["score"] = score
            detail["score_in_range"] = (
                isinstance(score, int)
                and expect["score_min"] <= score <= expect["score_max"]
            )
            gap = result.data.get("skill_gap") or []
            detail["gap_ok"] = all(g in gap for g in expect.get("gap_contains") or [])
            detail["skill_gap"] = gap
            ok = detail["score_in_range"] and detail["gap_ok"]
    except Exception as exc:  # noqa: BLE001
        detail["reason"] = f"{type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    return {"case_id": case.case_id, "kind": case.kind, "ok": ok,
            "latency_ms": latency, "detail": detail}


async def run_routing_case(case: Case, sup: Supervisor, emit: EmitFn) -> dict:
    """routing：直调 Supervisor.route()，按 plan 中的 agent 判定。"""
    t0 = time.perf_counter()
    payload = case.payload
    state = TaskState(dataset_id=payload["context"].get("dataset_id"), query=payload["query"])
    if payload["context"].get("resume"):
        state.context["resume"] = payload["context"]["resume"]
    detail: dict = {"correct": False, "expected": payload["expect_agent"]}
    ok = False
    try:
        steps = await sup.route(state, emit)
        agents = [s.agent for s in steps]
        detail["actual_agents"] = agents
        expected = payload["expect_agent"]
        # clarify：空计划；data/match：期望 agent 出现在计划中
        detail["correct"] = (not agents) if expected is None else (expected in agents)
        ok = detail["correct"]
    except Exception as exc:  # noqa: BLE001
        detail["reason"] = f"{type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    return {"case_id": case.case_id, "kind": case.kind, "ok": ok,
            "latency_ms": latency, "detail": detail}


async def run_error_case(case: Case, sup: Supervisor, emit: EmitFn) -> dict:
    """error：走完整 run_task，断言 graceful（终态明确且不崩）。

    designed_fail 的用例（不存在数据集/缺元数据/SQL 报错）额外断言
    failed_final 且 errors 非空；其余断言到达 completed/failed_final 终态。
    """
    t0 = time.perf_counter()
    payload = case.payload
    state = TaskState(dataset_id=payload.get("dataset_id") or None, query=payload["query"])
    state.context.update(payload.get("context") or {})
    detail: dict = {"graceful": False, "designed_fail": payload.get("designed_fail", False)}
    ok = False
    try:
        final_state = await sup.run_task(state, emit)
        status = final_state.status
        detail["status"] = status.value
        terminal = status in (TaskStatus.COMPLETED, TaskStatus.FAILED_FINAL)
        # graceful：到达终态；failed_final 必须有明确 error，completed 不应带 error
        if status is TaskStatus.FAILED_FINAL:
            has_error = bool(final_state.errors)
        else:
            has_error = not final_state.errors
        graceful = terminal and has_error
        if payload.get("designed_fail"):
            graceful = graceful and status is TaskStatus.FAILED_FINAL
        detail["graceful"] = graceful
        detail["error_count"] = len(final_state.errors)
        ok = graceful
    except Exception as exc:  # noqa: BLE001 - 崩溃即不 graceful
        detail["reason"] = f"任务崩溃 {type(exc).__name__}: {exc}"
    latency = int((time.perf_counter() - t0) * 1000)
    return {"case_id": case.case_id, "kind": case.kind, "ok": ok,
            "latency_ms": latency, "detail": detail}


# ---------- 汇总执行 ----------

async def run_cases(cases: list[Case]) -> list[dict]:
    """顺序执行 case 列表，返回逐 case 明细。"""
    sup = Supervisor(build_registry())
    emit = make_emit([])
    rows: list[dict] = []
    for case in cases:
        t0 = time.perf_counter()
        if case.kind == "nl2sql":
            row = await run_nl2sql_case(case)
        elif case.kind == "match":
            row = await run_match_case(case, emit)
        elif case.kind == "routing":
            row = await run_routing_case(case, sup, emit)
        elif case.kind == "error":
            row = await run_error_case(case, sup, make_emit([]))
        else:  # pragma: no cover
            row = {"case_id": case.case_id, "kind": case.kind, "ok": False,
                   "latency_ms": int((time.perf_counter() - t0) * 1000),
                   "detail": {"reason": f"未知 kind: {case.kind}"}}
        rows.append(row)
        if not row["ok"]:
            reason = row.get("detail", {}).get("reason") or "特征断言未通过"
            logger.info("case 失败 %s (%s): %s", case.case_id, case.kind, reason)
    return rows


def _markdown_table(summary: dict, llm_provider: str) -> str:
    """汇总结果的 Markdown 表。"""
    lines = [
        "# AgentInsight 评测报告",
        "",
        f"- 生成时间：{summary.get('generated_at', '-')}",
        f"- LLM：{llm_provider}",
        "",
        "| kind | count | ok | ok_rate | 关键指标 |",
        "|---|---|---|---|---|",
    ]
    s, m, r, e = summary["sql"], summary["match"], summary["routing"], summary["error"]
    lines.append(
        f"| nl2sql | {s['count']} | {s['count'] * s['sql_exec_ok_rate']:.0f} 执行成功 | "
        f"- | exec_ok={s['sql_exec_ok_rate']:.2%} feature_pass={s['sql_feature_pass_rate']:.2%} "
        f"json_valid={s['json_validity']:.2%} avg={s['avg_latency_ms']}ms p95={s['p95_latency_ms']}ms |"
    )
    lines.append(
        f"| match | {m['count']} | - | - | score_in_range={m['score_in_range_rate']:.2%} "
        f"gap_accuracy={m['gap_accuracy_rate']:.2%} avg={m['avg_latency_ms']}ms |"
    )
    lines.append(
        f"| routing | {r['count']} | - | {r['routing_accuracy']:.2%} | "
        f"avg={r['avg_latency_ms']}ms |"
    )
    lines.append(
        f"| error | {e['count']} | - | - | graceful={e['graceful_rate']:.2%} "
        f"avg={e['avg_latency_ms']}ms |"
    )
    lines.append(
        f"| **total** | {summary['total']} | {summary['total'] - summary['failed']} ok | "
        f"{summary['ok_rate']:.2%} | |"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：跑评测 → 写 JSON 报告 → 打印 Markdown 汇总表。"""
    parser = argparse.ArgumentParser(description="AgentInsight 评测 runner")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 个 case")
    parser.add_argument("--kind", choices=["nl2sql", "match", "routing", "error"],
                        default=None, help="只跑指定 kind")
    parser.add_argument("--out", default=None, help="报告输出路径（相对路径锚定仓库根）")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.kind:
        cases = [c for c in cases if c.kind == args.kind]
    if args.limit:
        cases = cases[: args.limit]

    t0 = time.perf_counter()
    rows = asyncio.run(run_cases(cases))
    elapsed = int((time.perf_counter() - t0) * 1000)

    client = get_llm_client()
    if getattr(client, "is_mock", False):
        llm_provider = "mock"
    else:
        try:
            from app.persistence.mysql import get_active_model_config

            active = get_active_model_config()
            llm_provider = (active or {}).get("model") or settings.LLM_MODEL or "openai"
        except Exception:
            llm_provider = settings.LLM_MODEL or "openai"

    summary = summarize(rows)
    summary["generated_at"] = datetime.now().isoformat()
    summary["elapsed_ms"] = elapsed
    report = {
        "generated_at": summary["generated_at"],
        "llm_provider": llm_provider,
        "summary": summary,
        "rows": rows,
    }

    out = Path(args.out) if args.out else default_report_path()
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(_markdown_table(summary, llm_provider))
    print(f"\n报告已写入: {out}")
    failed = [r for r in rows if not r["ok"]]
    print(f"失败 {len(failed)}/{len(rows)} 个 case（明细见报告 rows）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
