"""评测指标计算（CONTRACTS3 §2.2）。

rows 为 runner 产出的逐 case 明细：
{case_id, kind, ok, latency_ms, detail:{...}}，detail 内的布尔特征由各
kind 的指标函数消费。
"""
from __future__ import annotations

import math

from app.core.logging import get_logger

logger = get_logger(__name__)


def _rate(hits: int, total: int) -> float:
    """命中率，总量为 0 时返回 0.0。"""
    return round(hits / total, 4) if total else 0.0


def _latencies(rows: list[dict]) -> list[float]:
    return [float(r.get("latency_ms") or 0) for r in rows]


def _avg(rows: list[dict]) -> float:
    lats = _latencies(rows)
    return round(sum(lats) / len(lats), 1) if lats else 0.0


def _p95(rows: list[dict]) -> float:
    """p95：最近邻秩法（ceil(0.95*n) 第 1 个）。"""
    lats = sorted(_latencies(rows))
    if not lats:
        return 0.0
    idx = max(0, math.ceil(0.95 * len(lats)) - 1)
    return lats[idx]


def _flag_rate(rows: list[dict], key: str) -> float:
    hits = sum(1 for r in rows if (r.get("detail") or {}).get(key))
    return _rate(hits, len(rows))


def sql_metrics(rows: list[dict]) -> dict:
    """nl2sql 指标：执行成功率 / 结构特征通过率 / JSON 有效率 / 延迟。"""
    rows = [r for r in rows if r.get("kind") == "nl2sql"]
    return {
        "count": len(rows),
        "sql_exec_ok_rate": _flag_rate(rows, "sql_exec_ok"),
        "sql_feature_pass_rate": _flag_rate(rows, "feature_pass"),
        "json_validity": _flag_rate(rows, "json_valid"),
        "avg_latency_ms": _avg(rows),
        "p95_latency_ms": _p95(rows),
    }


def match_metrics(rows: list[dict]) -> dict:
    """match 指标：分数区间命中率 / 缺口准确率 / 平均延迟。"""
    rows = [r for r in rows if r.get("kind") == "match"]
    return {
        "count": len(rows),
        "score_in_range_rate": _flag_rate(rows, "score_in_range"),
        "gap_accuracy_rate": _flag_rate(rows, "gap_ok"),
        "avg_latency_ms": _avg(rows),
    }


def routing_metrics(rows: list[dict]) -> dict:
    """routing 指标：路由准确率。"""
    rows = [r for r in rows if r.get("kind") == "routing"]
    return {
        "count": len(rows),
        "routing_accuracy": _flag_rate(rows, "correct"),
        "avg_latency_ms": _avg(rows),
    }


def error_metrics(rows: list[dict]) -> dict:
    """error 指标：graceful 比例（终态明确、有 error 信息、不崩）。"""
    rows = [r for r in rows if r.get("kind") == "error"]
    return {
        "count": len(rows),
        "graceful_rate": _flag_rate(rows, "graceful"),
        "avg_latency_ms": _avg(rows),
    }


def summarize(all_rows: list[dict]) -> dict:
    """全量汇总：total / failed / 各 kind 指标合并。"""
    total = len(all_rows)
    failed = sum(1 for r in all_rows if not r.get("ok"))
    return {
        "total": total,
        "failed": failed,
        "ok_rate": _rate(total - failed, total),
        "sql": sql_metrics(all_rows),
        "match": match_metrics(all_rows),
        "routing": routing_metrics(all_rows),
        "error": error_metrics(all_rows),
    }
