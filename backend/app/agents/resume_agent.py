"""ResumeAgent：简历文本抽取 → LLM/规则结构化画像（CONTRACTS2 §3.1）。

解析链：PDF → mineru_api（配置了 token 且 parser_backend=mineru_api）→ 失败/未配置
降级 pymupdf 文本层；DOCX → python-docx 段落；TXT → 直读。所有阻塞调用经
asyncio.to_thread。落库 save_resume 延迟导入（A4 同波交付，失败仅告警不阻断）。

CONTRACTS3 §1.4：最外层 cache-aside——文件字节指纹 → Redis；HIT 时 0 次解析、
0 次 LLM，data 加 "cache":"hit"；MISS 走原路径后写缓存，data 加 "cache":"miss"。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.agents.base import AgentResult, BaseAgent, EmitFn, get_setting_safe, safe_emit
from app.agent_runtime.state import TaskState
from app.cache.keys import bytes_hash, resume_key
from app.cache.policies import TTL_RESUME
from app.cache.redis import get_json, set_json
from app.core.logging import get_logger

logger = get_logger(__name__)

# LLM 结构化抽取的 system prompt：只输出一个 JSON 画像对象
RESUME_PROFILE_SYSTEM_PROMPT = (
    "你是简历结构化助手。从给定的简历文本中抽取信息，"
    '只输出一个 JSON 对象：{"skills": [], "projects": [], "education": "", '
    '"experience_years": 0, "highlights": []}，不要输出任何其他文字或代码块标记。\n'
    "skills 为技能名词列表（保留原始大小写）；projects 为项目经历描述列表；"
    "education 为最高学历，取值如 博士/硕士/本科/大专；"
    "experience_years 为工作年限（整数，推断不出填 0）；highlights 为个人亮点列表。"
)

# 简历正文送给 LLM 的最大长度（超出截断，避免上下文爆炸）
_MAX_LLM_TEXT = 6000

# 规则兜底用的补充技能词表（crawler.parser.SKILL_KEYWORDS 之外的常见技能）
_EXTRA_SKILLS = [
    "Go", "Golang", "TypeScript", "Vue", "React Native", "PostgreSQL",
    "Spring", "Spring Boot", "Kubernetes", "TensorFlow", "PyTorch",
    "机器学习", "深度学习", "NLP", "持续集成",
]

# 补充 ASCII 技能词用词边界匹配，避免 Go 误命中 Good 等
_EXTRA_ASCII_PATTERNS = {
    kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
    for kw in _EXTRA_SKILLS
    if kw.isascii()
}
# 中文词无 ASCII 词边界，用包含匹配
_EXTRA_CJK_SKILLS = [kw for kw in _EXTRA_SKILLS if not kw.isascii()]

# 学历优先级从高到低
_EDU_LEVELS = ("博士", "硕士", "本科", "大专")
# 经验年限：如 "3 年工作经验" / "5年"
_YEARS_RE = re.compile(r"(\d{1,2})\s*年")


# ---------- 文本抽取（阻塞，均在 to_thread 中调用） ----------

def _pdf_text_pymupdf(path: str) -> str:
    """pymupdf 文本层兜底：逐页 get_text 拼接。"""
    import pymupdf  # 勿用 fitz 别名

    parts: list[str] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            parts.append(page.get_text())
    return "\n".join(parts)


def _docx_text(path: str) -> str:
    """python-docx：遍历段落拼接。"""
    import docx

    document = docx.Document(path)
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


def _txt_text(path: str) -> str:
    """TXT 直读，utf-8 宽容解码。"""
    return Path(path).read_text(encoding="utf-8", errors="ignore")


async def _extract_text(path: str) -> tuple[str, str]:
    """按后缀抽取纯文本，返回 (text, backend_used)。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        backend = await get_setting_safe("parser_backend", "mineru_api")
        token = await get_setting_safe("mineru_api_token", "")
        if backend == "mineru_api" and token.strip():
            try:
                from app.tools.mineru_client import parse_pdf

                text = await parse_pdf(path, token)
                if text.strip():
                    return text, "mineru_api"
            except ImportError as exc:
                logger.warning("mineru_client 导入失败，降级 pymupdf: %s", exc)
            except Exception as exc:  # noqa: BLE001 - 含 MineruError
                logger.warning("MinerU 解析失败，降级 pymupdf: %s", exc)
        return await asyncio.to_thread(_pdf_text_pymupdf, path), "pymupdf"
    if suffix == ".docx":
        return await asyncio.to_thread(_docx_text, path), "python-docx"
    return await asyncio.to_thread(_txt_text, path), "txt"


# ---------- 结构化画像 ----------

def _rule_skills(text: str) -> list[str]:
    """词表匹配技能：crawler 词表 + 补充词表，去重保序（保留词表原始大小写）。"""
    from app.crawler.parser import SKILL_KEYWORDS, _SKILL_PATTERNS

    skills: list[str] = []
    for kw in list(SKILL_KEYWORDS) + list(_EXTRA_ASCII_PATTERNS) + _EXTRA_CJK_SKILLS:
        pat = _SKILL_PATTERNS.get(kw) or _EXTRA_ASCII_PATTERNS.get(kw)
        hit = pat.search(text) if pat else kw in text
        if hit and kw not in skills:
            skills.append(kw)
    return skills


def _rule_profile(text: str) -> dict:
    """正则/词表兜底画像（mock LLM 或真实 LLM 失败时使用）。"""
    education = next((edu for edu in _EDU_LEVELS if edu in text), "")
    years_m = _YEARS_RE.search(text)
    projects = [
        line.strip()[:80]
        for line in text.splitlines()
        if "项目" in line and line.strip()
    ][:5]
    return {
        "skills": _rule_skills(text),
        "projects": projects,
        "education": education,
        "experience_years": int(years_m.group(1)) if years_m else 0,
        "highlights": [],
    }


def _coerce_profile(raw: object) -> dict:
    """把 LLM 返回收敛成标准画像结构（字段缺省、类型修正）。"""
    data = raw if isinstance(raw, dict) else {}
    skills = data.get("skills")
    projects = data.get("projects")
    highlights = data.get("highlights")
    years = data.get("experience_years")
    try:
        years_i = int(years)
    except (TypeError, ValueError):
        years_i = 0
    return {
        "skills": [str(s) for s in skills] if isinstance(skills, list) else [],
        "projects": [str(p) for p in projects] if isinstance(projects, list) else [],
        "education": str(data.get("education") or ""),
        "experience_years": max(0, years_i),
        "highlights": [str(h) for h in highlights] if isinstance(highlights, list) else [],
    }


def _is_mock_client(client: object) -> bool:
    """兼容 A4 前后的 llm.py：优先看 is_mock 属性，回退类型判断。"""
    from app.core.llm import MockLLMClient

    return getattr(client, "is_mock", isinstance(client, MockLLMClient)) is True


class ResumeAgent(BaseAgent):
    """简历解析 agent：产出结构化画像。"""

    name = "resume_agent"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        meta = state.context.get("resume") or {}
        path = meta.get("path")
        resume_id = meta.get("resume_id")
        filename = meta.get("filename") or (Path(str(path)).name if path else "")
        if not path or not resume_id:
            return AgentResult(
                status="error",
                message_type="resume_profile",
                data={},
                errors=["缺少简历元数据 state.context['resume'] (resume_id/path)"],
            )
        if not Path(str(path)).is_file():
            return AgentResult(
                status="error",
                message_type="resume_profile",
                data={},
                errors=[f"简历文件不存在: {path}"],
            )

        # 0. cache-aside（CONTRACTS3 §1.4）：文件字节指纹（不解析）→ Redis。
        #    HIT 直接用缓存画像，0 次文件解析 + 0 次 LLM。
        raw_bytes = await asyncio.to_thread(Path(str(path)).read_bytes)
        key = resume_key(bytes_hash(raw_bytes))
        cached = await get_json(key)
        hit = isinstance(cached, dict) and isinstance(cached.get("profile"), dict)
        await safe_emit(emit, {"type": "cache", "hit": hit, "key": key})

        if hit:
            profile = cached["profile"]
            text_chars = int(cached.get("text_chars") or 0)
            backend = str(cached.get("parse_backend") or "cache")
            source = str(cached.get("profile_source") or "cache")
            cache_state = "hit"
        else:
            # 1. 抽取文本（MinerU / pymupdf / docx / txt）
            text, backend = await _extract_text(str(path))
            if not text.strip():
                return AgentResult(
                    status="error",
                    message_type="resume_profile",
                    data={"resume_id": resume_id, "filename": filename},
                    errors=["简历解析结果为空文本"],
                )
            text_chars = len(text)
            cache_state = "miss"

            # 2. LLM 结构化（mock/失败 → 规则兜底）
            client = None
            try:
                from app.core.llm import get_llm_client

                client = get_llm_client()
            except Exception as exc:  # noqa: BLE001
                logger.warning("获取 LLM 客户端失败，走规则画像: %s", exc)
            if client is not None and not _is_mock_client(client):
                try:
                    profile = _coerce_profile(
                        await client.generate_json(
                            RESUME_PROFILE_SYSTEM_PROMPT, text[:_MAX_LLM_TEXT]
                        )
                    )
                    source = "llm"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("简历结构化 LLM 失败，走规则画像: %s", exc)
                    profile, source = _rule_profile(text), "mock"
            else:
                profile, source = _rule_profile(text), "mock"

            # 写缓存（失败静默，不影响主链路）
            await set_json(
                key,
                {
                    "profile": profile,
                    "text_chars": text_chars,
                    "parse_backend": backend,
                    "profile_source": source,
                },
                TTL_RESUME,
            )

        # 3. 落库（A4 提供 save_resume，失败仅告警不阻断；HIT/MISS 都落，持久化行为不变）
        try:
            from app.persistence.mysql import save_resume

            await asyncio.to_thread(save_resume, resume_id, filename, str(path), profile)
        except Exception as exc:  # noqa: BLE001
            logger.warning("save_resume 落库失败（不阻断）resume_id=%s: %s", resume_id, exc)

        data = {
            "resume_id": resume_id,
            "filename": filename,
            "profile": profile,
            "text_chars": text_chars,
            "parse_backend": backend,
            "profile_source": source,
            "cache": cache_state,
        }
        state.results["resume_agent"] = data
        return AgentResult(status="ok", message_type="resume_profile", data=data)
