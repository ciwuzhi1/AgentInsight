"""context 单测：token 估算 / 预算截断 / 上下文压缩 / prompt 组装。"""
from app.context.budget import TokenBudget, estimate_tokens
from app.context.builder import build_match_context, build_nl2sql_context
from app.context.compressor import (
    compress_job,
    compress_result,
    compress_resume,
    compress_schema,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_chinese_chars_are_one_token_each(self):
        assert estimate_tokens("你好世界") == 4

    def test_english_chars_are_quarter_token_each(self):
        # 4 个英文字符 ≈ 1 token
        assert estimate_tokens("abcd") == 1

    def test_mixed_text(self):
        # 2 中文 + 8 英文 → 2 + 8//4 = 4
        assert estimate_tokens("你好abcdefgh") == 4


class TestTokenBudgetCheck:
    def test_within_budget(self):
        budget = TokenBudget(max_input_tokens=10)
        assert budget.check_input("你好世界") is True

    def test_over_budget(self):
        budget = TokenBudget(max_input_tokens=2)
        assert budget.check_input("你好世界") is False

    def test_exactly_at_budget(self):
        budget = TokenBudget(max_input_tokens=4)
        assert budget.check_input("你好世界") is True

    def test_empty_text_within_any_budget(self):
        budget = TokenBudget(max_input_tokens=0)
        assert budget.check_input("") is True


class TestTokenBudgetTruncate:
    def test_short_text_unchanged(self):
        budget = TokenBudget(max_input_tokens=100)
        assert budget.truncate_input("你好世界") == "你好世界"

    def test_long_text_is_truncated(self):
        budget = TokenBudget(max_input_tokens=5)
        text = "这是一段非常长的中文文本用来测试截断逻辑"  # 18 tokens
        result = budget.truncate_input(text)
        assert len(result) < len(text)
        assert budget.check_input(result)

    def test_default_budget_keeps_reasonable_text(self):
        budget = TokenBudget()
        assert budget.truncate_input("统计各地区销售额") == "统计各地区销售额"


class TestCompressResume:
    def test_keeps_key_fields_only(self):
        profile = {
            "skills": ["Python", "SQL"],
            "projects": ["招聘数据分析平台"],
            "education": "本科",
            "experience_years": 3,
            "raw_text": "不该出现在压缩结果里的原文",
            "phone": "13800000000",
        }
        compact = compress_resume(profile)
        assert set(compact.keys()) == {
            "skills",
            "projects",
            "education",
            "experience_years",
        }
        assert compact["skills"] == ["Python", "SQL"]
        assert compact["education"] == "本科"
        assert compact["experience_years"] == 3

    def test_limits_skills_to_20(self):
        profile = {"skills": [f"skill{i}" for i in range(30)]}
        compact = compress_resume(profile)
        assert len(compact["skills"]) == 20

    def test_limits_projects_to_5_and_truncates_chars(self):
        profile = {"projects": [f"项目{i}" + "内容" * 40 for i in range(8)]}
        compact = compress_resume(profile)
        assert len(compact["projects"]) == 5
        assert all(len(p) <= 50 for p in compact["projects"])

    def test_empty_profile_uses_defaults(self):
        compact = compress_resume({})
        assert compact["skills"] == []
        assert compact["projects"] == []
        assert compact["education"] == ""
        assert compact["experience_years"] == 0


class TestCompressJob:
    def test_keeps_key_fields_only(self):
        job = {
            "title": "数据分析师",
            "must_have": ["SQL", "Python"],
            "nice_to_have": ["Tableau"],
            "skills": ["SQL", "Python"],
            "description": "很长的 JD 原文不应带出",
            "salary": "20-40K",
        }
        compact = compress_job(job)
        assert set(compact.keys()) == {
            "title",
            "must_have",
            "nice_to_have",
            "skills",
        }
        assert compact["title"] == "数据分析师"
        assert compact["must_have"] == ["SQL", "Python"]

    def test_limits_list_lengths(self):
        job = {
            "must_have": [f"m{i}" for i in range(15)],
            "nice_to_have": [f"n{i}" for i in range(10)],
            "skills": [f"s{i}" for i in range(20)],
        }
        compact = compress_job(job)
        assert len(compact["must_have"]) == 10
        assert len(compact["nice_to_have"]) == 5
        assert len(compact["skills"]) == 15

    def test_empty_job_uses_defaults(self):
        compact = compress_job({})
        assert compact["title"] == ""
        assert compact["must_have"] == []


class TestCompressSchema:
    def test_formats_columns_as_lines(self):
        schema = [
            {"name": "id", "type": "INTEGER"},
            {"name": "title", "type": "VARCHAR"},
        ]
        assert compress_schema(schema) == "id (INTEGER)\ntitle (VARCHAR)"

    def test_overflow_appends_count_note(self):
        schema = [{"name": f"c{i}", "type": "INT"} for i in range(25)]
        result = compress_schema(schema, max_cols=20)
        lines = result.split("\n")
        assert len(lines) == 21  # 20 列 + 1 行省略说明
        assert lines[-1] == "... 共 25 列"


class TestCompressResult:
    def test_formats_sample_rows(self):
        columns = ["region", "sales"]
        rows = [["北京", 100], ["上海", 200]]
        result = compress_result(columns, rows)
        assert result == "region | sales\n北京 | 100\n上海 | 200"

    def test_limits_rows(self):
        columns = ["id"]
        rows = [[i] for i in range(10)]
        result = compress_result(columns, rows)
        assert result.count("\n") == 5  # header + 5 样例行


class TestBuildMatchContext:
    def _resume(self):
        return {
            "skills": ["Python", "SQL", "机器学习"],
            "projects": ["招聘画像系统"],
            "education": "硕士",
            "experience_years": 5,
        }

    def _job(self, title: str, must_have: list[str] | None = None):
        return {
            "title": title,
            "must_have": must_have or ["SQL", "Python"],
            "nice_to_have": ["沟通能力"],
            "skills": ["SQL", "Python"],
        }

    def test_contains_resume_and_jobs(self):
        context = build_match_context(self._resume(), [self._job("数据分析师")])
        assert "简历画像" in context
        assert "岗位要求" in context
        assert "数据分析师" in context
        assert "SQL" in context
        assert "Python" in context

    def test_limits_jobs_to_5(self):
        jobs = [self._job(f"岗位{i}") for i in range(8)]
        context = build_match_context(self._resume(), jobs)
        assert "岗位4" in context
        assert "岗位5" not in context

    def test_limits_must_have_to_5_items(self):
        job = self._job("算法工程师", must_have=[f"技能{i}" for i in range(8)])
        context = build_match_context(self._resume(), [job])
        assert "技能4" in context
        assert "技能5" not in context

    def test_respects_budget_truncation(self):
        resume = self._resume()
        resume["skills"] = [f"很长的技能名称{i}" for i in range(20)]
        jobs = [
            self._job(f"岗位{i}", must_have=[f"要求{j}" for j in range(10)])
            for i in range(5)
        ]
        full = build_match_context(resume, jobs, budget=TokenBudget(max_input_tokens=10**9))
        context = build_match_context(resume, jobs, budget=TokenBudget(max_input_tokens=50))
        assert len(context) < len(full)


class TestBuildNl2sqlContext:
    def test_basic_context(self):
        context = build_nl2sql_context("id (INT)", "统计总销售额")
        assert "id (INT)" in context
        assert "统计总销售额" in context

    def test_includes_up_to_3_examples(self):
        examples = [
            {"question": f"问题{i}", "sql": f"SELECT {i}"}
            for i in range(5)
        ]
        context = build_nl2sql_context("t (INT)", "查询", examples=examples)
        assert "问题0" in context
        assert "问题2" in context
        assert "问题3" not in context
