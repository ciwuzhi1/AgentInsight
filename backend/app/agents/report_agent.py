"""ReportSynthesizer：汇总所有 Agent 结果生成最终报告。"""
from __future__ import annotations

import time

from app.agents.base import AgentResult, BaseAgent, EmitFn, get_setting_safe
from app.agent_runtime.state import TaskState
from app.core.logging import get_logger

logger = get_logger(__name__)

# 维度展示名
_DIMENSION_NAMES = {
    "skill": "技能匹配",
    "project": "项目经验",
    "experience": "工作年限",
    "education": "教育背景",
    "engineering": "工程能力",
}


class ReportSynthesizer(BaseAgent):
    """报告合成器：模板化汇总 + 可选 LLM 润色。"""

    name = "report_synthesizer"

    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult:
        t0 = time.perf_counter()

        # 收集所有 Agent 结果
        results = state.results

        # 根据任务类型选择报告模板
        if "match_agent" in results:
            report = self._match_report(results)
        elif "data_agent" in results:
            report = self._data_report(results)
        else:
            report = {"summary": "任务完成", "details": {}}

        # 可选 LLM 润色
        llm_enabled = await get_setting_safe("report_llm_enabled", "false") == "true"
        if llm_enabled:
            report = await self._llm_polish(report, results)

        data = {
            "report": report,
            "source": "llm" if llm_enabled else "template",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }

        state.results["report_synthesizer"] = data
        return AgentResult(status="ok", message_type="report", data=data)

    def _match_report(self, results: dict) -> dict:
        """匹配报告模板。"""
        match = results.get("match_agent", {})
        score = match.get("score", 0)
        dims = match.get("dimensions", {})
        gap = match.get("skill_gap", [])

        # 确定匹配等级
        if score >= 80:
            level = "优秀"
        elif score >= 60:
            level = "良好"
        elif score >= 40:
            level = "一般"
        else:
            level = "较低"

        return {
            "title": "简历匹配分析报告",
            "summary": f"综合匹配度 {score} 分（{level}）",
            "score": score,
            "level": level,
            "dimensions": dims,
            "skill_gap": gap[:10],
            "highlights": self._extract_highlights(match),
            "suggestions": self._generate_suggestions(score, gap),
        }

    def _data_report(self, results: dict) -> dict:
        """数据分析报告模板。"""
        data = results.get("data_agent", {})
        final = data.get("final", {})

        return {
            "title": "数据分析报告",
            "summary": final.get("explanation", "分析完成"),
            "query": final.get("query", ""),
            "engine": final.get("engine", ""),
            "row_count": final.get("row_count", 0),
            "columns": final.get("columns", []),
            "elapsed_ms": final.get("elapsed_ms", 0),
        }

    def _extract_highlights(self, match: dict) -> list[str]:
        """提取亮点。"""
        highlights = []
        dims = match.get("dimensions", {})

        for dim, score in dims.items():
            if score >= 80:
                highlights.append(f"{_DIMENSION_NAMES.get(dim, dim)}：{score} 分")

        return highlights

    def _generate_suggestions(self, score: int, gap: list[str]) -> list[str]:
        """生成建议。"""
        suggestions = []

        if score < 60:
            suggestions.append("建议提升核心技能以增加匹配度")

        if gap:
            top_gaps = gap[:3]
            suggestions.append(f"优先补齐：{', '.join(top_gaps)}")

        return suggestions

    async def _llm_polish(self, report: dict, results: dict) -> dict:
        """LLM 润色报告。"""
        try:
            from app.core.llm import MockLLMClient, get_llm_client

            client = get_llm_client()
            if getattr(client, "is_mock", isinstance(client, MockLLMClient)):
                return report

            prompt = f"""请根据以下数据生成一份简洁的分析报告（100字以内）：

{report.get('summary', '')}
维度：{report.get('dimensions', {})}
缺口：{report.get('skill_gap', [])[:5]}

只输出报告文本，不要其他内容。"""

            raw = await client.generate_json("你是报告生成助手", prompt)
            polished = str((raw or {}).get("report", "")).strip()

            if polished:
                report["polished_summary"] = polished
                report["source"] = "llm"
        except Exception as exc:  # noqa: BLE001 - 润色失败不阻断报告
            logger.warning("LLM 润色失败，使用模板: %s", exc)

        return report
