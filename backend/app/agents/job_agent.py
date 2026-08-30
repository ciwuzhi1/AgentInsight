"""JobAgent：岗位输入 → LLM/规则结构化画像（CONTRACTS2 §3.2）。

输入：state.context["jobs"]（API 按 job_ids 查好）或 context["jd_text"]。
LLM 结构化 {jobs:[{id,title,must_have,nice_to_have,skills}],level}；
mock 路径直接用 skills 逗号串拆分 + extract_skills(description) 关键词。
"""
from __future__ import annotations

import re

from app.agents.base import AgentResult, BaseAgent, EmitFn
from app.agent_runtime.state import TaskState
from app.core.logging import get_logger

logger = get_logger(__name__)

# LLM 结构化抽取的 system prompt：只输出一个 JSON 对象
JOB_PROFILE_SYSTEM_PROMPT = (
    "你是岗位结构化助手。对给定的每个岗位抽取要求，只输出一个 JSON 对象："
    '{"jobs": [{"id": 0, "title": "", "must_have": [], "nice_to_have": [], "skills": []}],'
    ' "level": ""}，不要输出任何其他文字或代码块标记。\n'
    "must_have 为必备要求列表；nice_to_have 为加分项列表；skills 为技能词列表"
    "（保留原始大小写）；level 为这些岗位的整体级别（如 初级/中级/高级，推断不出留空）。"
    "id 沿用输入岗位的 id。"
)

# skills 字段的分隔符（逗号/顿号/分号/斜杠）
_SPLIT_RE = re.compile(r"[,，、;；/]+")

# 送给 LLM 的岗位上下文上限
_MAX_LLM_JOBS = 20


def _coerce_job(raw: object, fallback: dict | None = None) -> dict:
    """把单个岗位结构收敛成标准形态，缺省字段用输入岗位兜底。"""
    fb = fallback or {}
    data = raw if isinstance(raw, dict) else {}
    must = data.get("must_have")
    nice = data.get("nice_to_have")
    skills = data.get("skills")
    return {
        "id": data.get("id") if data.get("id") is not None else fb.get("id"),
        "title": data.get("title") or fb.get("title") or "",
        "company": fb.get("company") or "",
        "must_have": [str(s) for s in must] if isinstance(must, list) else [],
        "nice_to_have": [str(s) for s in nice] if isinstance(nice, list) else [],
        "skills": [str(s) for s in skills] if isinstance(skills, list) else [],
    }


def _dedup_keep_order(items: list[str]) -> list[str]:
    """去重保序（strip 后比较，保留首个原始写法）。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if key and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


def _mock_job(job: dict) -> dict:
    """规则版岗位画像：skills 逗号串拆分 + description 关键词抽取。"""
    from app.crawler.parser import extract_skills

    raw_skills = job.get("skills") or ""
    if isinstance(raw_skills, list):
        parts = [str(s) for s in raw_skills]
    else:
        parts = [p.strip() for p in _SPLIT_RE.split(str(raw_skills)) if p.strip()]
    skills = _dedup_keep_order(parts + extract_skills(str(job.get("description") or "")))
    return {
        "id": job.get("id"),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "must_have": skills,
        "nice_to_have": [],
        "skills": skills,
    }


class JobAgent(BaseAgent):
    """岗位解析 agent：产出结构化岗位要求。"""

    name = "job_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        jobs = state.context.get("jobs")
        jd_text = state.context.get("jd_text")

        # 无结构化输入时把 jd_text 包成一个匿名岗位
        if not jobs and jd_text:
            jobs = [{"id": None, "title": "JD", "company": "", "skills": "",
                     "description": str(jd_text)}]
        if not jobs or not isinstance(jobs, list):
            return AgentResult(
                status="error",
                message_type="job_profile",
                data={},
                errors=["缺少岗位输入 state.context['jobs'] / 'jd_text'"],
            )

        # LLM 结构化（mock/失败 → 规则拆分）
        client = None
        try:
            from app.core.llm import get_llm_client

            client = get_llm_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取 LLM 客户端失败，走规则画像: %s", exc)

        source = "mock"
        profiled: list[dict] | None = None
        if client is not None:
            try:
                from app.core.llm import MockLLMClient

                # A4 之后看 is_mock 属性，之前回退类型判断
                if not getattr(client, "is_mock", isinstance(client, MockLLMClient)):
                    batch = jobs[:_MAX_LLM_JOBS]
                    user = f"岗位列表（JSON）:\n{batch}\n"
                    raw = await client.generate_json(JOB_PROFILE_SYSTEM_PROMPT, user)
                    raw_jobs = raw.get("jobs") if isinstance(raw, dict) else None
                    if isinstance(raw_jobs, list) and raw_jobs:
                        profiled = [
                            _coerce_job(r, jobs[i] if i < len(jobs) else None)
                            for i, r in enumerate(raw_jobs)
                        ]
                        source = "llm"
            except Exception as exc:  # noqa: BLE001
                logger.warning("岗位结构化 LLM 失败，走规则画像: %s", exc)

        if profiled is None:
            profiled = [_mock_job(dict(job)) for job in jobs if isinstance(job, dict)]
            source = "mock"

        level = ""
        data = {"jobs": profiled, "level": level, "source": source}
        state.results["job_agent"] = data
        return AgentResult(status="ok", message_type="job_profile", data=data)
