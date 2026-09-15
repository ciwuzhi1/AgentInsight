"""MatchAgent：简历画像 × 岗位画像 → 规则打分 + 技能缺口 + 解读（CONTRACTS2 §3.3）。

输入只从 state.messages 取 receiver 含 "match_agent" 的消息（resume_profile /
job_profile），找不到时兜底读 state.results。规则打分不依赖 LLM；LLM 解读由
app_settings.match_llm_enabled 开关控制且仅真模型启用，失败回模板文案。
"""
from __future__ import annotations

import re
import time

from app.agents.base import AgentResult, BaseAgent, EmitFn, get_setting_safe
from app.agent_runtime.state import TaskState
from app.core.logging import get_logger

logger = get_logger(__name__)

# LLM 解读的 system prompt：只输出一个 JSON 对象
MATCH_INTERPRET_SYSTEM_PROMPT = (
    "你是求职匹配分析助手。根据给出的简历摘要、岗位与匹配分数，"
    '只输出一个 JSON 对象：{"interpretation": "不超过120字的中文解读"}，'
    "不要输出任何其他文字或代码块标记。解读需点出优势、主要技能缺口与建议。"
)

# 同义词归一表（每组第一个为规范形，全部小写比较；规范形取完整写法）
_SYNONYM_GROUPS: list[tuple[str, ...]] = [
    ("javascript", "js"),
    ("kubernetes", "k8s"),
    ("机器学习", "ml"),
    ("postgresql", "postgres"),
    ("python", "py"),
    ("typescript", "ts"),
    ("go", "golang"),
    ("vue", "vue.js"),
    ("spring boot", "springboot"),
    ("ci/cd", "持续集成", "ci"),
    ("docker", "容器"),
    ("git", "版本控制"),
]
# 缩写 -> 规范形
_CANON: dict[str, str] = {
    syn: group[0] for group in _SYNONYM_GROUPS for syn in group
}

# 分项权重（CONTRACTS2 §3.3）
_WEIGHTS = {"skill": 0.5, "project": 0.2, "experience": 0.1, "education": 0.1, "engineering": 0.1}

# 工程化技能（engineering 维度，用规范形）
_ENGINEERING_SKILLS = {"docker", "kubernetes", "git", "ci/cd", "linux"}

# 教育背景分
_EDU_SCORE = {"博士": 100, "硕士": 100, "本科": 80, "大专": 60}

# JD 经验要求：如 "3年" / "3 年以上"
_YEARS_RE = re.compile(r"(\d{1,2})\s*年")


def _norm(skill: str) -> str:
    """技能归一：lower/trim。"""
    return str(skill).strip().lower()


def normalize_skill(skill: str) -> str:
    """技能归一：lower/trim + 同义词表映射到规范形。"""
    n = _norm(skill)
    return _CANON.get(n, n)


def _skill_set(skills: list) -> set[str]:
    """技能列表 → 归一集合；同时返回展示用的 归一形->原始形 映射。"""
    display: dict[str, str] = {}
    for s in skills:
        n = normalize_skill(str(s))
        if n and n not in display:
            display[n] = str(s).strip()
    return set(display), display


def _extract_required_years(jobs: list[dict]) -> int | None:
    """从 JD 的 must_have/nice_to_have/skills 文本中抓经验年限要求；无则 None。"""
    for job in jobs:
        for field in ("must_have", "nice_to_have", "skills"):
            for item in job.get(field) or []:
                m = _YEARS_RE.search(str(item))
                if m:
                    return int(m.group(1))
    return None


def _education_score(education: str) -> int:
    return _EDU_SCORE.get(_norm(education), 50)


# ---------- 消息收集 ----------

def _receiver_text(receiver: object) -> str:
    """receiver 兼容 list / str。"""
    if isinstance(receiver, (list, tuple)):
        return " ".join(str(r) for r in receiver)
    return str(receiver or "")


def collect_payloads(state: TaskState) -> tuple[dict | None, dict | None]:
    """从 state.messages 找发给 match_agent 的 resume_profile / job_profile 消息。

    找不到消息时兜底读 state.results（resume_agent / job_agent 的 data）。
    """
    resume_payload: dict | None = None
    job_payload: dict | None = None
    for msg in getattr(state, "messages", []) or []:
        if "match_agent" not in _receiver_text(getattr(msg, "receiver", None)):
            continue
        mtype = getattr(msg, "type", "")
        payload = getattr(msg, "payload", None)
        if mtype == "resume_profile" and isinstance(payload, dict):
            resume_payload = payload
        elif mtype == "job_profile" and isinstance(payload, dict):
            job_payload = payload
    if resume_payload is None:
        data = state.results.get("resume_agent")
        if isinstance(data, dict) and data:
            resume_payload = data
    if job_payload is None:
        data = state.results.get("job_agent")
        if isinstance(data, dict) and data:
            job_payload = data
    return resume_payload, job_payload


# ---------- 打分 ----------

def compute_match(profile: dict, jobs: list[dict]) -> dict:
    """规则打分：返回 score/dimensions/skill_gap。"""
    profile = profile or {}
    jobs = jobs or []
    jd_skills: list[str] = []
    for job in jobs:
        jd_skills.extend([str(s) for s in job.get("skills") or []])
        jd_skills.extend([str(s) for s in job.get("must_have") or []])

    resume_norm_set, _ = _skill_set(profile.get("skills") or [])
    jd_norm_set, jd_display = _skill_set(jd_skills)

    # skill：resume 技能集与各 JD 技能集交集比例的均值
    if jd_norm_set:
        ratios = []
        for job in jobs:
            jset, _ = _skill_set(
                [str(s) for s in job.get("skills") or []]
                + [str(s) for s in job.get("must_have") or []]
            )
            ratios.append(len(resume_norm_set & jset) / len(jset) if jset else 1.0)
        skill_score = round(sum(ratios) / len(ratios) * 100)
    else:
        skill_score = 100

    # project：projects 非空且与 JD 技能有关键词命中给满分，有项目无命中 60
    projects = [str(p) for p in profile.get("projects") or []]
    if not projects:
        project_score = 0
    else:
        project_text = " ".join(projects).lower()
        hit = any(normalize_skill(s) in project_text for s in jd_norm_set)
        project_score = 100 if hit else 60

    # experience：JD 无经验要求满分，有则 resume 年限/要求 线性封顶
    required_years = _extract_required_years(jobs)
    if required_years is None or required_years <= 0:
        experience_score = 100
    else:
        years = profile.get("experience_years") or 0
        try:
            experience_score = min(100, round(float(years) / required_years * 100))
        except (TypeError, ValueError):
            experience_score = 0

    education_score = _education_score(str(profile.get("education") or ""))

    # engineering：工程化技能命中比例
    if _ENGINEERING_SKILLS:
        hits = len(resume_norm_set & _ENGINEERING_SKILLS)
        engineering_score = round(hits / len(_ENGINEERING_SKILLS) * 100)
    else:  # pragma: no cover
        engineering_score = 0

    dimensions = {
        "skill": int(skill_score),
        "project": int(project_score),
        "experience": int(experience_score),
        "education": int(education_score),
        "engineering": int(engineering_score),
    }
    score = round(sum(dimensions[k] * w for k, w in _WEIGHTS.items()))
    # 技能缺口：JD 技能并集减 resume 技能集（展示 JD 侧原始大小写）
    skill_gap = [jd_display[n] for n in sorted(jd_display) if n not in resume_norm_set]
    return {"score": int(score), "dimensions": dimensions, "skill_gap": skill_gap}


# ---------- 解读 ----------

def _template_interpretation(score: int, dimensions: dict, skill_gap: list[str]) -> str:
    """无 LLM 时的模板文案。"""
    gap = "、".join(skill_gap[:3]) + ("等" if len(skill_gap) > 3 else "") if skill_gap else "无"
    return (
        f"综合匹配 {score} 分：技能 {dimensions['skill']}、项目 {dimensions['project']}、"
        f"工程化 {dimensions['engineering']}。技能缺口：{gap}。"
        f"建议优先补齐缺口技能并强化相关项目经历。"
    )


async def _llm_interpretation(
    profile: dict, score: int, dimensions: dict, skill_gap: list[str]
) -> str | None:
    """真模型解读；开关关闭/mock/异常返回 None（回模板）。"""
    if await get_setting_safe("match_llm_enabled", "true") != "true":
        return None
    try:
        from app.core.llm import MockLLMClient, get_llm_client

        client = get_llm_client()
        if getattr(client, "is_mock", isinstance(client, MockLLMClient)):
            return None
        summary = (
            f"简历摘要: 技能 {profile.get('skills') or []}，"
            f"学历 {profile.get('education') or '未知'}，"
            f"年限 {profile.get('experience_years') or 0} 年。\n"
            f"匹配分数: {score}，分项 {dimensions}，技能缺口 {skill_gap[:8]}。"
        )
        raw = await client.generate_json(MATCH_INTERPRET_SYSTEM_PROMPT, summary)
        text = str((raw or {}).get("interpretation") or "").strip()
        return text[:120] if text else None
    except Exception as exc:  # noqa: BLE001 - 解读失败不阻断匹配结果
        logger.warning("LLM 匹配解读失败，回模板文案: %s", exc)
        return None


class MatchAgent(BaseAgent):
    """匹配打分 agent：产出 score/dimensions/skill_gap/interpretation。"""

    name = "match_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        resume_payload, job_payload = collect_payloads(state)
        if not resume_payload or not isinstance(resume_payload.get("profile"), dict):
            return AgentResult(
                status="error",
                message_type="match_result",
                data={},
                errors=["缺少 resume_profile 输入（messages/results 均未找到）"],
            )
        profile = resume_payload["profile"]
        jobs = job_payload.get("jobs") if job_payload else None
        jobs = [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []

        # 1. 规则打分
        scored = compute_match(profile, jobs)
        score, dimensions = scored["score"], scored["dimensions"]
        skill_gap = scored["skill_gap"]

        # 2. LLM 解读（开关 + 真模型；否则模板）
        interp = await _llm_interpretation(profile, score, dimensions, skill_gap)
        if interp:
            interpretation, interp_source = interp, "llm"
        else:
            interpretation = _template_interpretation(score, dimensions, skill_gap)
            interp_source = "mock"

        data = {
            "score": score,
            "dimensions": dimensions,
            "skill_gap": skill_gap,
            "interpretation": interpretation,
            "interpretation_source": interp_source,
            "resume": {
                "resume_id": resume_payload.get("resume_id"),
                "filename": resume_payload.get("filename"),
                "skills": profile.get("skills") or [],
                "experience_years": profile.get("experience_years") or 0,
                "education": profile.get("education") or "",
            },
            "jobs": [
                {"id": j.get("id"), "title": j.get("title"), "company": j.get("company")}
                for j in jobs
            ],
        }
        state.results["match_agent"] = data
        return AgentResult(status="ok", message_type="match_result", data=data)
