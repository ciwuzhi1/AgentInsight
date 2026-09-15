"""Context 集成测试：真实 agent 输出 → 上下文构建 → token 预算 → 压缩比。

覆盖：context builder 与真实 agent 数据的端到端集成、
TokenBudget 强制执行、压缩比验证。
"""
from __future__ import annotations

from app.context.budget import TokenBudget, estimate_tokens
from app.context.builder import build_match_context, build_nl2sql_context
from app.context.compressor import (
    compress_job,
    compress_result,
    compress_resume,
    compress_schema,
)


# ---------- 上下文构建器与真实 agent 输出的集成 ----------


class TestContextBuilderWithAgentOutputs:
    def test_build_match_context_from_agent_results(self):
        """用 match_agent 真实输出结构构建上下文：包含简历 + 岗位关键信息。"""
        resume_profile = {
            "skills": ["Python", "SQL", "机器学习", "Docker", "Kubernetes"],
            "projects": ["招聘画像系统", "数据分析平台"],
            "education": "硕士",
            "experience_years": 5,
        }
        jobs = [
            {
                "title": "数据分析师",
                "must_have": ["SQL", "Python", "统计学"],
                "nice_to_have": ["Tableau"],
                "skills": ["SQL", "Python"],
            },
            {
                "title": "后端工程师",
                "must_have": ["Python", "Django", "PostgreSQL"],
                "nice_to_have": ["Redis"],
                "skills": ["Python", "Django"],
            },
        ]

        context = build_match_context(resume_profile, jobs)

        assert "简历画像" in context
        assert "岗位要求" in context
        assert "数据分析师" in context
        assert "后端工程师" in context
        assert "Python" in context
        assert "SQL" in context
        assert "硕士" in context
        assert "5" in context  # experience_years

    def test_build_nl2sql_context_from_schema(self):
        """用 compress_schema 输出构建 NL2SQL 上下文。"""
        schema = [
            {"name": "id", "type": "INTEGER"},
            {"name": "region", "type": "VARCHAR"},
            {"name": "sales", "type": "DOUBLE"},
        ]
        schema_str = compress_schema(schema)
        context = build_nl2sql_context(schema_str, "统计各地区销售额")

        assert "id (INTEGER)" in context
        assert "region (VARCHAR)" in context
        assert "sales (DOUBLE)" in context
        assert "统计各地区销售额" in context

    def test_build_nl2sql_context_with_few_shot_examples(self):
        """带 few-shot 示例的 NL2SQL 上下文：最多 3 条。"""
        examples = [
            {"question": f"问题{i}", "sql": f"SELECT {i} FROM t"} for i in range(5)
        ]
        context = build_nl2sql_context("t (INT)", "查询", examples=examples)

        assert "问题0" in context
        assert "问题1" in context
        assert "问题2" in context
        assert "问题3" not in context  # 第 4 条被截断

    def test_context_builder_uses_compressed_data(self):
        """build_match_context 内部调用 compress_resume/compress_job：
        超长 skills 被截断到 20，超长 projects 截断到 5。"""
        resume = {
            "skills": [f"skill_{i}" for i in range(30)],
            "projects": [f"project_{i}" + "很长的内容" * 20 for i in range(10)],
            "education": "本科",
            "experience_years": 3,
            "phone": "13800000000",  # 不应出现在压缩结果中
            "raw_text": "原始简历文本不应出现在上下文",
        }
        jobs = [{"title": "岗位", "must_have": [f"req_{i}" for i in range(15)]}]

        context = build_match_context(resume, jobs)

        # 超长列表被截断
        assert "skill_19" in context  # 前 20 个
        assert "skill_25" not in context  # 超出的
        assert "原始简历文本" not in context  # 未压缩字段
        assert "13800000000" not in context


# ---------- Token 预算强制执行 ----------


class TestTokenBudgetEnforcement:
    def test_default_budget_keeps_normal_context(self):
        """默认 4000 token 预算内，正常大小的上下文不被截断。"""
        budget = TokenBudget()  # max_input_tokens=4000
        resume = {
            "skills": ["Python", "SQL"],
            "projects": ["项目A"],
            "education": "本科",
            "experience_years": 3,
        }
        jobs = [{"title": "分析师", "must_have": ["SQL"]}]

        context = build_match_context(resume, jobs, budget=budget)
        # 正常大小的上下文不应被截断（长度不变）
        unlimited = build_match_context(
            resume, jobs, budget=TokenBudget(max_input_tokens=10**9)
        )
        assert context == unlimited

    def test_small_budget_truncates_context(self):
        """极小预算强制截断：输出长度 < 原始长度。"""
        resume = {
            "skills": [f"很长的技能名称_{i}" for i in range(20)],
            "projects": ["非常长的项目描述" * 10],
            "education": "博士",
            "experience_years": 10,
        }
        jobs = [
            {
                "title": f"岗位_{i}",
                "must_have": [f"要求_{j}" for j in range(10)],
            }
            for i in range(5)
        ]

        full = build_match_context(
            resume, jobs, budget=TokenBudget(max_input_tokens=10**9)
        )
        truncated = build_match_context(
            resume, jobs, budget=TokenBudget(max_input_tokens=50)
        )

        assert len(truncated) < len(full)
        assert estimate_tokens(truncated) <= 50 + 10  # 允许少量截断误差

    def test_budget_check_input_boundary(self):
        """check_input 在边界值处正确判断。"""
        budget = TokenBudget(max_input_tokens=10)
        assert budget.check_input("你好世界") is True  # 4 tokens
        assert budget.check_input("你好世界你好世界你好") is True  # 10 tokens
        assert budget.check_input("你好世界你好世界你好你好") is False  # 12 tokens

    def test_truncate_preserves_prefix(self):
        """截断保留原文前缀（不会从中间或末尾开始）。"""
        budget = TokenBudget(max_input_tokens=5)
        text = "这是开头" + "很长的中间内容" * 10 + "结尾"
        result = budget.truncate_input(text)
        assert result.startswith("这是开头")

    def test_nl2sql_context_respects_budget(self):
        """NL2SQL 上下文本身不调用 budget（builder 不传 budget），
        但 TokenBudget 可独立用于外层截断。"""
        schema_str = compress_schema(
            [{"name": f"col_{i}", "type": "VARCHAR"} for i in range(50)]
        )
        context = build_nl2sql_context(schema_str, "查询所有数据")

        budget = TokenBudget(max_input_tokens=30)
        truncated = budget.truncate_input(context)
        assert len(truncated) < len(context)
        assert budget.check_input(truncated)


# ---------- 压缩比验证 ----------


class TestCompressionRatios:
    def test_compress_resume_reduces_field_count(self):
        """compress_resume 只保留 4 个字段，原始可能有更多。"""
        profile = {
            "skills": ["Python", "SQL", "Java"],
            "projects": ["项目A"],
            "education": "本科",
            "experience_years": 3,
            "phone": "13800000000",
            "email": "test@example.com",
            "address": "北京市海淀区",
            "raw_text": "完整简历原文..." * 100,
            "certifications": ["PMP", "AWS"],
            "languages": ["中文", "英文"],
        }
        compact = compress_resume(profile)
        # 只保留 4 个关键字段
        assert set(compact.keys()) == {
            "skills",
            "projects",
            "education",
            "experience_years",
        }
        # 数据量显著减少
        original_size = sum(len(str(v)) for v in profile.values())
        compact_size = sum(len(str(v)) for v in compact.values())
        assert compact_size < original_size

    def test_compress_job_reduces_field_count(self):
        """compress_job 只保留 4 个字段。"""
        job = {
            "title": "数据分析师",
            "must_have": ["SQL", "Python"],
            "nice_to_have": ["Tableau"],
            "skills": ["SQL", "Python"],
            "description": "很长的 JD 原文" * 200,
            "salary": "20-40K",
            "location": "北京",
            "company": "某大厂",
            "benefits": ["五险一金", "弹性工作"],
        }
        compact = compress_job(job)
        assert set(compact.keys()) == {
            "title",
            "must_have",
            "nice_to_have",
            "skills",
        }

    def test_compress_schema_overflow_note(self):
        """超过 max_cols 的 schema 添加省略说明行。"""
        schema = [{"name": f"c{i}", "type": "INT"} for i in range(30)]
        result = compress_schema(schema, max_cols=10)
        lines = result.split("\n")
        assert len(lines) == 11  # 10 列 + 1 行省略
        assert lines[-1] == "... 共 30 列"

    def test_compress_result_limits_sample_rows(self):
        """compress_result 最多保留 max_rows 行样例。"""
        columns = ["id", "name"]
        rows = [[i, f"name_{i}"] for i in range(100)]
        result = compress_result(columns, rows, max_rows=3)
        lines = result.split("\n")
        assert len(lines) == 4  # header + 3 行
        assert lines[0] == "id | name"

    def test_full_pipeline_compression_ratio(self):
        """完整管道：原始 agent 输出 → 压缩 → 上下文，体积大幅缩小。"""
        # 模拟真实 agent 输出（包含大量冗余字段）
        raw_resume = {
            "skills": [f"skill_{i}" for i in range(50)],
            "projects": [f"project_{i}" + "详细描述" * 50 for i in range(20)],
            "education": "硕士",
            "experience_years": 8,
            "phone": "13800000000",
            "email": "user@example.com",
            "raw_text": "完整简历内容" * 500,
            "certifications": [f"cert_{i}" for i in range(10)],
            "summary": "个人简介" * 100,
        }
        raw_jobs = [
            {
                "title": f"岗位_{i}",
                "must_have": [f"req_{j}" for j in range(20)],
                "nice_to_have": [f"nice_{j}" for j in range(10)],
                "skills": [f"skill_{j}" for j in range(20)],
                "description": "JD 描述" * 200,
                "salary": "30-50K",
                "location": "上海",
            }
            for i in range(10)
        ]

        # 原始 JSON 大小
        import json

        original_json = json.dumps({"resume": raw_resume, "jobs": raw_jobs}, ensure_ascii=False)
        original_tokens = estimate_tokens(original_json)

        # 通过 context builder（内部压缩）
        context = build_match_context(
            raw_resume, raw_jobs, budget=TokenBudget(max_input_tokens=10**9)
        )
        context_tokens = estimate_tokens(context)

        # 压缩比：context 应显著小于原始 JSON
        compression_ratio = context_tokens / max(original_tokens, 1)
        assert compression_ratio < 0.3, (
            f"压缩比 {compression_ratio:.2%} 过高，原始 {original_tokens} tokens，"
            f"压缩后 {context_tokens} tokens"
        )

    def test_compress_resume_project_char_limit(self):
        """项目描述截断到 50 字符。"""
        profile = {"projects": ["A" * 100, "B" * 30, "C" * 50]}
        compact = compress_resume(profile)
        assert len(compact["projects"][0]) == 50
        assert len(compact["projects"][1]) == 30  # 短的不截断
        assert len(compact["projects"][2]) == 50  # 恰好 50 不截断
