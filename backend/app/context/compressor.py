"""上下文压缩：提取关键信息，减少 token 消耗。"""
from __future__ import annotations


def compress_resume(profile: dict) -> dict:
    """压缩简历画像：只保留关键字段。"""
    return {
        "skills": profile.get("skills", [])[:20],
        "projects": [p[:50] for p in profile.get("projects", [])[:5]],
        "education": profile.get("education", ""),
        "experience_years": profile.get("experience_years", 0),
    }


def compress_job(job: dict) -> dict:
    """压缩 JD：只保留关键字段。"""
    return {
        "title": job.get("title", ""),
        "must_have": job.get("must_have", [])[:10],
        "nice_to_have": job.get("nice_to_have", [])[:5],
        "skills": job.get("skills", [])[:15],
    }


def compress_schema(schema: list[dict], max_cols: int = 20) -> str:
    """压缩 schema 为字符串。"""
    cols = schema[:max_cols]
    lines = [f"{c['name']} ({c['type']})" for c in cols]
    if len(schema) > max_cols:
        lines.append(f"... 共 {len(schema)} 列")
    return "\n".join(lines)


def compress_result(columns: list[str], rows: list, max_rows: int = 5) -> str:
    """压缩查询结果为样例字符串。"""
    header = " | ".join(columns)
    sample = rows[:max_rows]
    body = "\n".join(" | ".join(str(v) for v in row) for row in sample)
    return f"{header}\n{body}"
