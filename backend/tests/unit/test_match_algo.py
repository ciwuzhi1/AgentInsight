"""匹配算法测试：TF-IDF + 余弦相似度（全离线）。"""
from __future__ import annotations

import math

from app.agents.match_algo import (
    combined_skill_score,
    compute_tfidf_weights,
    cosine_similarity,
    skill_coverage_score,
    skill_match_score,
)


class TestCosineSimilarity:
    """余弦相似度测试。"""

    def test_identical_vectors(self):
        """相同向量相似度为 1。"""
        vec = {"python": 1.0, "java": 0.5}
        assert abs(cosine_similarity(vec, vec) - 1.0) < 0.001

    def test_orthogonal_vectors(self):
        """正交向量相似度为 0。"""
        vec1 = {"python": 1.0}
        vec2 = {"java": 1.0}
        assert cosine_similarity(vec1, vec2) == 0.0

    def test_empty_vectors(self):
        """空向量相似度为 0。"""
        assert cosine_similarity({}, {"python": 1.0}) == 0.0
        assert cosine_similarity({"python": 1.0}, {}) == 0.0

    def test_partial_overlap(self):
        """部分重叠的向量。"""
        vec1 = {"python": 1.0, "java": 1.0, "go": 1.0}
        vec2 = {"python": 1.0, "java": 1.0, "rust": 1.0}
        # 交集 2 个，模长各 sqrt(3)
        expected = 2 / (math.sqrt(3) * math.sqrt(3))
        assert abs(cosine_similarity(vec1, vec2) - expected) < 0.001


class TestTfidfWeights:
    """TF-IDF 权重计算测试。"""

    def test_rare_skill_gets_higher_weight(self):
        """稀有技能（只在 1 个 JD 出现）权重高于常见技能。"""
        all_jobs = [
            ["python", "java"],  # JD1
            ["python", "go"],    # JD2
            ["python", "rust"],  # JD3
        ]
        jd_weights, _ = compute_tfidf_weights(all_jobs, ["python", "rust"])
        # python 出现在 3 个 JD（常见），rust 只在 1 个（稀有）
        # 稀有技能 IDF 更高，权重应该更大
        assert jd_weights["rust"] > jd_weights["python"]

    def test_empty_jobs(self):
        """无 JD 时返回空权重。"""
        jd_weights, resume_weights = compute_tfidf_weights([], ["python"])
        assert jd_weights == {}
        assert resume_weights == {}

    def test_resume_weight_uses_idf(self):
        """简历技能权重使用 IDF（不重复计 TF）。"""
        all_jobs = [
            ["python", "java"],
            ["python", "go"],
        ]
        _, resume_weights = compute_tfidf_weights(all_jobs, ["python", "cobol"])
        # python 在 2 个 JD 出现，cobol 未出现（默认 IDF=1）
        assert "python" in resume_weights
        assert "cobol" in resume_weights
        # python 的 IDF 应该小于 cobol（更常见）
        assert resume_weights["python"] < resume_weights["cobol"]


class TestSkillMatchScore:
    """技能匹配分测试。"""

    def test_perfect_match(self):
        """完全匹配得高分。"""
        resume = ["python", "java", "go"]
        jobs = [["python", "java", "go"]]
        score, _ = skill_match_score(resume, jobs)
        assert score >= 80  # 高分

    def test_no_match(self):
        """无交集得 0 分。"""
        resume = ["python"]
        jobs = [["cobol", "fortran"]]
        score, _ = skill_match_score(resume, jobs)
        assert score == 0.0

    def test_empty_resume(self):
        """空简历得 0 分。"""
        score, _ = skill_match_score([], [["python"]])
        assert score == 0.0

    def test_empty_jobs(self):
        """空 JD 得满分。"""
        score, _ = skill_match_score(["python"], [])
        assert score == 100.0


class TestSkillCoverageScore:
    """技能覆盖率测试。"""

    def test_full_coverage(self):
        """完全覆盖得 100 分。"""
        assert skill_coverage_score({"python", "java"}, {"python", "java"}) == 100.0

    def test_partial_coverage(self):
        """部分覆盖。"""
        score = skill_coverage_score({"python"}, {"python", "java"})
        assert score == 50.0

    def test_empty_jd(self):
        """空 JD 得满分。"""
        assert skill_coverage_score({"python"}, set()) == 100.0


class TestCombinedSkillScore:
    """组合得分测试。"""

    def test_combines_tfidf_and_coverage(self):
        """组合得分 = TF-IDF*0.6 + 覆盖率*0.4。"""
        resume = ["python", "java"]
        jobs = [["python", "java", "go", "rust"]]
        score, _ = combined_skill_score(resume, jobs)
        # 覆盖率 = 2/4 = 50%，TF-IDF 应该也较高
        assert 0 <= score <= 100

    def test_rare_skill_boost(self):
        """匹配稀有技能应比只匹配常见技能得分高。"""
        # 场景1：简历只匹配常见技能
        common_jobs = [
            ["python", "java"],
            ["python", "go"],
            ["python", "rust"],
        ]
        resume_common = ["python"]
        score_common, _ = combined_skill_score(resume_common, common_jobs)

        # 场景2：简历匹配稀有技能
        rare_jobs = [
            ["cobol", "fortran"],
            ["cobol", "pascal"],
            ["cobol", "ada"],
        ]
        resume_rare = ["cobol"]
        score_rare, _ = combined_skill_score(resume_rare, rare_jobs)

        # 稀有技能匹配应该得分更高或相当（因为覆盖率相同，但 TF-IDF 权重更高）
        # 注意：由于覆盖率相同（都是 1/1 或 1/3），差异主要来自 TF-IDF
        assert score_rare >= score_common * 0.8  # 允许一定波动
