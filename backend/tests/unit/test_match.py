"""MatchAgent 单测（CONTRACTS2 §3.3 / §7）：规则打分、同义词归一、缺口、mock 解读。

全程离线：app_settings.get_setting 与 llm.get_llm_client 均被 monkeypatch 掉。
"""
import asyncio

import pytest

from app.agent_runtime.message import make_message
from app.agent_runtime.state import TaskState
from app.agents import match_agent as match_mod
from app.agents.match_agent import MatchAgent, compute_match, normalize_skill

_WEIGHTS = {"skill": 0.5, "project": 0.2, "experience": 0.1, "education": 0.1, "engineering": 0.1}


def build_state() -> TaskState:
    """手工构造 messages：resume_profile + job_profile 发给 match_agent。"""
    state = TaskState(query="简历与岗位匹配")
    resume_payload = {
        "resume_id": "r1",
        "filename": "resume.pdf",
        "profile": {
            "skills": ["Python", "K8s", "Git"],
            "projects": ["云原生数据平台，覆盖 Kubernetes 部署与 CI/CD"],
            "education": "本科",
            "experience_years": 3,
            "highlights": [],
        },
    }
    job_payload = {
        "jobs": [
            {
                "id": 10,
                "title": "云原生后端工程师",
                "company": "ACME",
                "skills": ["Kubernetes", "Python", "Docker"],
                "must_have": [],
            }
        ]
    }
    state.messages.append(make_message(
        state.task_id, "resume_agent", ["match_agent"], "resume_profile", resume_payload))
    state.messages.append(make_message(
        state.task_id, "job_agent", ["match_agent"], "job_profile", job_payload))
    return state


def test_score_and_dimensions_in_valid_range():
    """score/dimensions 区间合法：五键齐全、0~100、score 为加权取整。"""
    profile = {
        "skills": ["Python", "K8s", "Git"],
        "projects": ["云原生数据平台，覆盖 Kubernetes 部署与 CI/CD"],
        "education": "本科",
        "experience_years": 3,
    }
    jobs = [{
        "id": 10, "title": "云原生后端", "company": "ACME",
        "skills": ["Kubernetes", "Python", "Docker"], "must_have": [],
    }]
    scored = compute_match(profile, jobs)

    dims = scored["dimensions"]
    assert set(dims) == {"skill", "project", "experience", "education", "engineering"}
    for v in dims.values():
        assert isinstance(v, int) and 0 <= v <= 100
    score = scored["score"]
    assert isinstance(score, int) and 0 <= score <= 100
    assert score == round(sum(dims[k] * w for k, w in _WEIGHTS.items()))

    # K8s ≡ Kubernetes（同义词归一）不进缺口；Docker 缺失进缺口
    assert scored["skill_gap"] == ["Docker"]


def test_synonym_normalization():
    """同义词归一：k8s/kubernetes、js/javascript、ml/机器学习 等映射到规范形。"""
    assert normalize_skill("K8s") == "kubernetes"
    assert normalize_skill(" kubernetes ") == "kubernetes"
    assert normalize_skill("JS") == "javascript"
    # 同义词组规范形取组内第一个词（中文全称）
    assert normalize_skill("ml") == "机器学习"
    assert normalize_skill("postgres") == "postgresql"

    # k8s 写法的简历 × Kubernetes 写法的 JD：归一后无缺口、技能分满分
    profile = {"skills": ["k8s", "js"], "projects": [], "education": "本科", "experience_years": 0}
    jobs = [{"id": 1, "title": "后端", "company": "C", "skills": ["K8s", "JavaScript"], "must_have": []}]
    scored = compute_match(profile, jobs)
    assert scored["skill_gap"] == []
    assert scored["dimensions"]["skill"] == 100


def test_skill_gap_computation():
    """缺口 = JD 技能并集（含 must_have）减 resume 技能集，保留 JD 原始大小写。"""
    profile = {"skills": ["Python"], "projects": [], "education": "本科", "experience_years": 1}
    jobs = [{
        "id": 2, "title": "平台工程师", "company": "B",
        "skills": ["Docker", "Kubernetes", "Python"],
        "must_have": ["Go"],
    }]
    scored = compute_match(profile, jobs)
    # 归一 key 排序：docker < go < kubernetes
    assert scored["skill_gap"] == ["Docker", "Go", "Kubernetes"]

    # 经验分：JD 要求 3 年，resume 3 年 → 满分；resume 1 年 → 线性 33；无要求 → 满分
    profile_3y = dict(profile, experience_years=3)
    jobs_years = [{"id": 3, "title": "t", "company": "c", "skills": [], "must_have": ["3 年以上经验"]}]
    assert compute_match(profile_3y, jobs_years)["dimensions"]["experience"] == 100
    assert compute_match(profile, jobs_years)["dimensions"]["experience"] == 33
    assert compute_match(profile, [{"id": 4, "title": "t", "company": "c", "skills": []}])[
        "dimensions"]["experience"] == 100


class _FakeMockLLMClient:
    """带 is_mock 标记的假客户端；若被调用即失败。"""

    is_mock = True

    async def generate_json(self, system: str, user: str) -> dict:  # pragma: no cover
        raise AssertionError("mock 客户端不应被用于生成解读")


def test_match_agent_mock_interpretation(monkeypatch):
    """开关开 + mock 客户端 → interpretation_source == 'mock'，走模板文案。"""
    monkeypatch.setattr("app.core.app_settings.get_setting", lambda key, default=None: "true")
    monkeypatch.setattr("app.core.llm.get_llm_client", lambda: _FakeMockLLMClient())

    async def main():
        state = build_state()
        result = await MatchAgent().run(state, lambda event: None)
        return state, result

    state, result = asyncio.run(main())
    assert result.status == "ok"
    assert result.message_type == "match_result"

    data = result.data
    assert data["interpretation_source"] == "mock"
    assert data["interpretation"].startswith("综合匹配")
    assert isinstance(data["score"], int) and 0 <= data["score"] <= 100
    assert data["skill_gap"] == ["Docker"]

    # resume/jobs 摘要
    assert data["resume"]["resume_id"] == "r1"
    assert data["resume"]["skills"] == ["Python", "K8s", "Git"]
    assert data["jobs"] == [{"id": 10, "title": "云原生后端工程师", "company": "ACME"}]

    # 结果写回 state.results 供 validator / final 组装
    assert state.results["match_agent"] is data
