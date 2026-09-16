"""匹配算法：TF-IDF + 余弦相似度（替代简单集合交集）。

核心思想：
- TF (Term Frequency)：技能在单个 JD 中的出现频率
- IDF (Inverse Document Frequency)：技能在所有 JD 中的稀有度（越稀有权重越高）
- 余弦相似度：衡量简历技能向量与 JD 技能向量的夹角

优势：
- 区分"Python"（常见，权重低）和"COBOL"（稀有，权重高）
- 匹配稀有技能的简历得分更高
- 数学上有理论支撑，面试可讲
"""
from __future__ import annotations

import math
from collections import Counter


def compute_tfidf_weights(
    all_jobs_skills: list[list[str]],
    resume_skills: list[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """计算 TF-IDF 权重。

    Args:
        all_jobs_skills: 所有 JD 的技能列表（每个 JD 一个技能列表）
        resume_skills: 简历技能列表

    Returns:
        (jd_weights, resume_weights): 技能 → 权重 的映射
    """
    n_docs = len(all_jobs_skills)
    if n_docs == 0:
        return {}, {}

    # 计算每个技能的文档频率 (DF)
    df: Counter[str] = Counter()
    for skills in all_jobs_skills:
        # 去重：同一 JD 内同一技能只算一次
        unique_skills = set(skills)
        for skill in unique_skills:
            df[skill] += 1

    # 计算 IDF: log(N / (DF + 1)) + 1（加 1 平滑，避免除零）
    idf: dict[str, float] = {}
    for skill, freq in df.items():
        idf[skill] = math.log(n_docs / (freq + 1)) + 1

    # 计算 JD 权重：TF * IDF
    # TF = 技能在 JD 中出现次数 / JD 技能总数
    jd_weights: dict[str, float] = {}
    for skills in all_jobs_skills:
        tf = Counter(skills)
        total = len(skills) if skills else 1
        for skill, count in tf.items():
            tf_val = count / total
            idf_val = idf.get(skill, 1.0)
            weight = tf_val * idf_val
            # 取最大权重（同一技能在多个 JD 中出现时）
            if skill not in jd_weights or weight > jd_weights[skill]:
                jd_weights[skill] = weight

    # 计算简历权重：只用 IDF（简历技能不重复计 TF）
    resume_weights: dict[str, float] = {}
    resume_unique = set(resume_skills)
    for skill in resume_unique:
        resume_weights[skill] = idf.get(skill, 1.0)  # 未见技能用默认 IDF=1

    return jd_weights, resume_weights


def cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """计算两个稀疏向量的余弦相似度。

    Args:
        vec1: 技能 → 权重
        vec2: 技能 → 权重

    Returns:
        余弦相似度 [0, 1]
    """
    # 取交集维度
    common_keys = set(vec1.keys()) & set(vec2.keys())
    if not common_keys:
        return 0.0

    # 点积
    dot = sum(vec1[k] * vec2[k] for k in common_keys)

    # 模长
    norm1 = math.sqrt(sum(v * v for v in vec1.values()))
    norm2 = math.sqrt(sum(v * v for v in vec2.values()))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot / (norm1 * norm2)


def skill_match_score(
    resume_skills: list[str],
    all_jobs_skills: list[list[str]],
) -> tuple[float, dict[str, float]]:
    """计算 TF-IDF + 余弦相似度的技能匹配分。

    Args:
        resume_skills: 简历技能列表（已归一化）
        all_jobs_skills: 所有 JD 的技能列表（已归一化）

    Returns:
        (score, skill_weights): 匹配分 [0, 100]，各技能的 TF-IDF 权重
    """
    if not all_jobs_skills or not resume_skills:
        return 100.0 if not all_jobs_skills else 0.0, {}

    # 计算 TF-IDF 权重
    jd_weights, resume_weights = compute_tfidf_weights(all_jobs_skills, resume_skills)

    # 构建向量：只保留双方都有的技能
    common_skills = set(jd_weights.keys()) & set(resume_weights.keys())
    if not common_skills:
        return 0.0, jd_weights

    # 余弦相似度
    similarity = cosine_similarity(jd_weights, resume_weights)

    # 转换为 0-100 分
    # 余弦相似度通常较小（稀疏向量），用平方根放大
    score = round(math.sqrt(similarity) * 100)

    return float(min(100, score)), jd_weights


def skill_coverage_score(
    resume_skills: set[str],
    jd_skills: set[str],
) -> float:
    """技能覆盖率：JD 技能被简历覆盖的比例。

    与 TF-IDF 互补：覆盖率保证"有多少 JD 要求被满足"，
    TF-IDF 保证"匹配到的技能有多重要"。
    """
    if not jd_skills:
        return 100.0
    coverage = len(resume_skills & jd_skills) / len(jd_skills)
    return round(coverage * 100, 1)


def combined_skill_score(
    resume_skills: list[str],
    all_jobs_skills: list[list[str]],
) -> tuple[float, dict[str, float]]:
    """组合得分：TF-IDF 余弦相似度 + 技能覆盖率。

    两者加权平均，兼顾"匹配质量"和"匹配数量"。
    """
    tfidf_score, weights = skill_match_score(resume_skills, all_jobs_skills)

    # 计算覆盖率（用所有 JD 技能的并集）
    jd_union: set[str] = set()
    for skills in all_jobs_skills:
        jd_union.update(skills)
    coverage = skill_coverage_score(set(resume_skills), jd_union)

    # 加权：TF-IDF 60% + 覆盖率 40%
    combined = round(tfidf_score * 0.6 + coverage * 0.4)

    return float(min(100, combined)), weights
