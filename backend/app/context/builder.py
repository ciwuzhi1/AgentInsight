"""上下文构建器：组装 LLM prompt。"""
from __future__ import annotations

from app.context.budget import TokenBudget
from app.context.compressor import compress_job, compress_resume


def build_match_context(
    resume: dict, jobs: list[dict], budget: TokenBudget | None = None
) -> str:
    """构建匹配任务的上下文。"""
    budget = budget or TokenBudget()

    resume_compact = compress_resume(resume)
    jobs_compact = [compress_job(j) for j in jobs[:5]]  # 最多 5 个 JD

    context = f"简历画像:\n{resume_compact}\n\n岗位要求:\n"
    for i, job in enumerate(jobs_compact, 1):
        context += f"{i}. {job['title']}\n"
        context += f"   必备: {', '.join(job['must_have'][:5])}\n"

    return budget.truncate_input(context)


def build_nl2sql_context(
    schema_str: str, query: str, examples: list[dict] | None = None
) -> str:
    """构建 NL2SQL 上下文。"""
    context = f"表结构:\n{schema_str}\n\n用户问题: {query}"

    if examples:
        context += "\n\n参考示例:\n"
        for ex in examples[:3]:
            context += f"问题: {ex.get('question', '')}\nSQL: {ex.get('sql', '')}\n"

    return context
