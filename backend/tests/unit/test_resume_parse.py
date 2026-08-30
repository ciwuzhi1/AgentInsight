"""ResumeAgent TXT 解析单测（CONTRACTS2 §3.1 / §7）：正则兜底 profile 与缺失文件报错。

不依赖 MinerU / 真实 LLM / MySQL：get_llm_client 固定返回 MockLLMClient，
save_resume 被替换为 no-op。
"""
import asyncio

from app.core.llm import MockLLMClient
from app.core import llm as llm_mod
from app.persistence import mysql as mysql_mod
from app.agents.resume_agent import ResumeAgent
from app.agent_runtime.state import TaskState

RESUME_TEXT = """张三
Python 后端工程师，3 年工作经验
学历：本科
熟悉 Python、Docker、MySQL
项目：电商平台重构，负责订单与支付链路
"""


def test_txt_resume_rule_profile(tmp_path, monkeypatch):
    """TXT 简历 → 规则兜底画像：skills/education/experience_years/projects 合理。"""
    path = tmp_path / "resume.txt"
    path.write_text(RESUME_TEXT, encoding="utf-8")
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda: MockLLMClient())
    monkeypatch.setattr(mysql_mod, "save_resume", lambda *args, **kwargs: None)

    async def main():
        state = TaskState(query="简历与岗位匹配")
        state.context["resume"] = {
            "resume_id": "r1",
            "path": str(path),
            "filename": "resume.txt",
        }
        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        result = await ResumeAgent().run(state, emit)
        return state, result

    state, result = asyncio.run(main())
    assert result.status == "ok"
    assert result.message_type == "resume_profile"
    assert result.data["resume_id"] == "r1"
    assert result.data["filename"] == "resume.txt"
    assert result.data["parse_backend"] == "txt"
    assert result.data["profile_source"] == "mock"  # mock 客户端 → 规则画像
    assert result.data["text_chars"] > 0

    profile = result.data["profile"]
    assert "Python" in profile["skills"]
    assert "Docker" in profile["skills"]
    assert profile["education"] == "本科"
    assert profile["experience_years"] == 3
    assert profile["projects"] and "电商平台重构" in profile["projects"][0]

    # 结果写回 state.results
    assert state.results["resume_agent"] is result.data


def test_missing_resume_file_returns_error(tmp_path):
    """文件缺失 → AgentResult.status == 'error'，错误信息指出路径。"""

    async def main():
        state = TaskState(query="简历与岗位匹配")
        missing = tmp_path / "nope.txt"
        state.context["resume"] = {
            "resume_id": "r2",
            "path": str(missing),
            "filename": "nope.txt",
        }
        result = await ResumeAgent().run(state, lambda event: None)
        return result

    result = asyncio.run(main())
    assert result.status == "error"
    assert result.errors and "不存在" in result.errors[0]
    assert str(tmp_path / "nope.txt") in result.errors[0]
