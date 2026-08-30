"""Planner：任务执行计划模板与 DAG 校验（CONTRACTS2 §1.1）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings


@dataclass
class PlanStep:
    """计划中的一个执行步骤。"""

    id: str                      # 步骤名，如 "resume"/"job"/"match"/"data"/"validator"
    agent: str                   # agent 注册名
    depends_on: list[str] = field(default_factory=list)  # 依赖的 step id
    params: dict = field(default_factory=dict)           # params.get("optional")=True 时失败可跳过


class PlanError(Exception):
    """计划非法：依赖缺失 / 成环 / 超步数上限。"""


def build_match_plan() -> list[PlanStep]:
    """简历-岗位匹配链路：resume/job 并行 → match → validator。"""
    return [
        PlanStep(id="resume", agent="resume_agent"),
        PlanStep(id="job", agent="job_agent"),
        PlanStep(
            id="match",
            agent="match_agent",
            depends_on=["resume", "job"],
            params={"optional": False},
        ),
        PlanStep(id="validator", agent="validator_agent", depends_on=["match"]),
    ]


def build_data_plan() -> list[PlanStep]:
    """数据分析链路：data → validator。"""
    return [
        PlanStep(id="data", agent="data_agent"),
        PlanStep(id="validator", agent="validator_agent", depends_on=["data"]),
    ]


def validate_dag(steps: list[PlanStep], max_steps: int | None = None) -> list[PlanStep]:
    """校验计划并返回拓扑序步骤列表；非法抛 PlanError。

    规则：步数 ≤ max_steps（默认 settings.MAX_AGENT_STEPS）、id 唯一、
    依赖必须存在、无环（Kahn 拓扑排序排完即无环）。
    """
    if max_steps is None:
        max_steps = settings.MAX_AGENT_STEPS
    if len(steps) > max_steps:
        raise PlanError(f"步骤数 {len(steps)} 超过上限 {max_steps}")

    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        raise PlanError("步骤 id 重复")
    by_id = {s.id: s for s in steps}

    for s in steps:
        for dep in s.depends_on:
            if dep not in by_id:
                raise PlanError(f"步骤 {s.id} 依赖不存在的步骤: {dep}")

    indegree = {s.id: 0 for s in steps}
    dependents: dict[str, list[str]] = {s.id: [] for s in steps}
    for s in steps:
        for dep in s.depends_on:
            indegree[s.id] += 1
            dependents[dep].append(s.id)

    queue = [sid for sid, deg in indegree.items() if deg == 0]
    ordered: list[PlanStep] = []
    while queue:
        sid = queue.pop(0)
        ordered.append(by_id[sid])
        for nxt in dependents[sid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(ordered) != len(steps):
        raise PlanError("计划存在循环依赖")
    return ordered
