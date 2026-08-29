"""TaskState 状态机单测（契约 §3.1/§14）。"""
import pytest

from app.agent_runtime.state import TaskState, TaskStatus
from app.core.config import settings


def _run_until_failed(state: TaskState) -> None:
    """推到 running 再落 failed，模拟 supervisor 单步异常路径。"""
    state.transition(TaskStatus.ROUTING)
    state.transition(TaskStatus.RUNNING)
    state.transition(TaskStatus.FAILED)


def test_initial_state_is_created():
    s = TaskState(dataset_id="ds1", query="按地区统计销售额")
    assert s.status is TaskStatus.CREATED
    assert s.retry_count == 0
    assert s.current_step == 0
    assert s.task_id  # uuid4 已生成


def test_happy_path_transitions():
    s = TaskState(query="q")
    s.transition(TaskStatus.ROUTING)
    s.transition(TaskStatus.RUNNING)
    s.transition(TaskStatus.VALIDATING)
    s.transition(TaskStatus.COMPLETED)
    assert s.status is TaskStatus.COMPLETED


def test_failed_retry_chain():
    s = TaskState(query="q")
    _run_until_failed(s)
    s.transition(TaskStatus.RETRYING)
    s.transition(TaskStatus.RUNNING)
    assert s.status is TaskStatus.RUNNING
    assert s.retry_count == 0  # retry_count 由 supervisor 维护，状态机不自动加


def test_illegal_transition_created_to_completed():
    s = TaskState(query="q")
    with pytest.raises(ValueError):
        s.transition(TaskStatus.COMPLETED)


def test_illegal_transition_created_to_running():
    s = TaskState(query="q")
    with pytest.raises(ValueError):
        s.transition(TaskStatus.RUNNING)


def test_completed_is_terminal():
    s = TaskState(query="q")
    s.transition(TaskStatus.ROUTING)
    s.transition(TaskStatus.RUNNING)
    s.transition(TaskStatus.VALIDATING)
    s.transition(TaskStatus.COMPLETED)
    with pytest.raises(ValueError):
        s.transition(TaskStatus.RUNNING)


def test_failed_final_is_terminal():
    s = TaskState(query="q")
    _run_until_failed(s)
    s.transition(TaskStatus.FAILED_FINAL)
    with pytest.raises(ValueError):
        s.transition(TaskStatus.RETRYING)


def test_retry_exhausted_goes_failed_final(monkeypatch):
    """模拟 supervisor._enter_retry：重试次数耗尽后 failed 只能进 failed_final。"""
    monkeypatch.setattr(settings, "MAX_RETRY", 2)
    s = TaskState(query="q")
    _run_until_failed(s)
    while s.retry_count < settings.MAX_RETRY:
        s.retry_count += 1
        s.transition(TaskStatus.RETRYING)
        s.transition(TaskStatus.RUNNING)
        s.transition(TaskStatus.FAILED)
    assert s.retry_count == settings.MAX_RETRY
    s.transition(TaskStatus.FAILED_FINAL)
    assert s.status is TaskStatus.FAILED_FINAL
