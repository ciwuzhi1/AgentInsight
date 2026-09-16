"""planner 单测（CONTRACTS2 §1.1 / §7）：模板结构 + validate_dag 非法计划。"""
import pytest

from app.core.config import settings
from app.agent_runtime.planner import (
    PlanError,
    PlanStep,
    build_data_plan,
    build_match_plan,
    validate_dag,
)


def test_match_plan_structure():
    """resume/job 无依赖，match 依赖二者，validator 依赖 match，report 依赖 validator。"""
    steps = build_match_plan()
    by_id = {s.id: s for s in steps}
    assert set(by_id) == {"resume", "job", "match", "validator", "report"}

    assert by_id["resume"].depends_on == []
    assert by_id["job"].depends_on == []
    assert by_id["match"].depends_on == ["resume", "job"]
    assert by_id["validator"].depends_on == ["match"]
    assert by_id["report"].depends_on == ["validator"]

    assert by_id["resume"].agent == "resume_agent"
    assert by_id["job"].agent == "job_agent"
    assert by_id["match"].agent == "match_agent"
    assert by_id["validator"].agent == "validator_agent"
    assert by_id["report"].agent == "report_synthesizer"

    # match 是必经步骤（失败不可跳过），report 可选
    assert not by_id["match"].params.get("optional")
    assert by_id["report"].params.get("optional") is True


def test_match_plan_topological_order():
    """validate_dag 返回拓扑序：resume/job 在 match 前，match 在 validator 前，report 最后。"""
    ordered = validate_dag(build_match_plan())
    ids = [s.id for s in ordered]
    assert set(ids) == {"resume", "job", "match", "validator", "report"}
    assert ids.index("match") > ids.index("resume")
    assert ids.index("match") > ids.index("job")
    assert ids.index("validator") > ids.index("match")
    assert ids.index("report") > ids.index("validator")


def test_data_plan_structure():
    """data 无依赖，validator 依赖 data，report 依赖 validator。"""
    steps = build_data_plan()
    assert [s.id for s in steps] == ["data", "validator", "report"]
    assert steps[0].depends_on == []
    assert steps[1].depends_on == ["data"]
    assert steps[2].depends_on == ["validator"]
    assert steps[0].agent == "data_agent"
    assert steps[1].agent == "validator_agent"
    assert steps[2].agent == "report_synthesizer"

    ordered = validate_dag(steps)
    assert [s.id for s in ordered] == ["data", "validator", "report"]


def test_validate_dag_cycle_raises():
    """环依赖抛 PlanError。"""
    steps = [
        PlanStep(id="a", agent="x", depends_on=["b"]),
        PlanStep(id="b", agent="y", depends_on=["a"]),
    ]
    with pytest.raises(PlanError):
        validate_dag(steps)


def test_validate_dag_missing_dependency_raises():
    """依赖不存在的步骤抛 PlanError。"""
    steps = [PlanStep(id="a", agent="x", depends_on=["ghost"])]
    with pytest.raises(PlanError):
        validate_dag(steps)


def test_validate_dag_max_steps_raises():
    """步数超过上限抛 PlanError（用 9 步超过 MAX_AGENT_STEPS=8）。"""
    steps = [
        PlanStep(id=f"s{i}", agent="x", depends_on=([f"s{i-1}"] if i else []))
        for i in range(settings.MAX_AGENT_STEPS + 1)
    ]
    with pytest.raises(PlanError):
        validate_dag(steps)
