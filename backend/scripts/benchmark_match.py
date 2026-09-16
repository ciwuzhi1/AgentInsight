"""匹配算法 benchmark：TF-IDF + 余弦相似度在不同技能规模下的耗时。

用法：python scripts/benchmark_match.py [--n 100]
维度：
- 不同 JD 语料规模（10/50/100/500 个岗位）
- 不同简历技能数（10/20/50）
- 拆分测 compute_tfidf_weights / cosine_similarity / skill_match_score / combined_skill_score
- 计时用 time.perf_counter()，输出 均值/中位/P95

结果为实测值，不预填。
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agents.match_algo import (  # noqa: E402
    combined_skill_score,
    compute_tfidf_weights,
    cosine_similarity,
    skill_coverage_score,
    skill_match_score,
)

# ---------------------------------------------------------------------------
# 统计与表格
# ---------------------------------------------------------------------------


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(len(xs) * p)))
    return xs[k]


def record(layer: str, name: str, times_ms: list[float], extra: str = "") -> None:
    mean = statistics.fmean(times_ms)
    med = statistics.median(times_ms)
    p95 = pct(times_ms, 0.95)
    mx = max(times_ms)
    note = f" {extra}" if extra else ""
    print(
        f"[{layer}] {name:<42} n={len(times_ms):<4} "
        f"mean={mean:>9.4f}ms  median={med:>9.4f}ms  p95={p95:>9.4f}ms  max={mx:>9.4f}ms{note}"
    )


# ---------------------------------------------------------------------------
# 合成语料
# ---------------------------------------------------------------------------

_SKILL_UNIVERSE = [
    "python", "sql", "java", "javascript", "docker", "kubernetes", "aws",
    "linux", "react", "spark", "mysql", "redis", "fastapi", "css", "html",
    "pandas", "git", "go", "typescript", "vue", "postgresql", "spring boot",
    "tensorflow", "pytorch", "机器学习", "深度学习", "nlp", "ci/cd",
    "kafka", "flink", "airflow", "etl", "微服务", "grpc", "nginx", "terraform",
    "ansible", "mongodb", "elasticsearch", "rabbitmq", "celery", "django",
    "flask", "node.js", "deno", "rust", "c++", "scala", "hadoop", "hive",
    "presto", "clickhouse", "superset", "tableau", "power bi", "looker",
    "数据仓库", "数据湖", "特征工程", "推荐系统", "风控", "ab 测试",
]


def make_job_corpus(n_jobs: int, skills_per_job: int = 12) -> list[list[str]]:
    """生成 n_jobs 条 JD 技能列表，技能从词典循环采样，保证可复现。"""
    corpus: list[list[str]] = []
    u = len(_SKILL_UNIVERSE)
    for i in range(n_jobs):
        start = (i * 7) % u
        skills = [_SKILL_UNIVERSE[(start + k) % u] for k in range(skills_per_job)]
        corpus.append(skills)
    return corpus


def make_resume_skills(n_skills: int) -> list[str]:
    return [_SKILL_UNIVERSE[(i * 3) % len(_SKILL_UNIVERSE)] for i in range(n_skills)]


def time_fn(fn, n: int) -> list[float]:
    """运行 fn n 次，返回每次耗时（ms）。fn 无参，每次调用应完成一次完整计算。"""
    ts: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return ts


# ---------------------------------------------------------------------------
# 基准项
# ---------------------------------------------------------------------------

JOB_SIZES = [10, 50, 100, 500]
RESUME_SIZES = [10, 20, 50]


def bench_tfidf(n: int) -> None:
    print("\n--- TF-IDF 权重计算（compute_tfidf_weights）---")
    for n_jobs in JOB_SIZES:
        corpus = make_job_corpus(n_jobs)
        for n_sk in (20,):
            resume = make_resume_skills(n_sk)
            ts = time_fn(lambda c=corpus, r=resume: compute_tfidf_weights(c, r), n)
            record("tfidf", f"jobs={n_jobs:<4} resume_skills={n_sk}", ts,
                   extra=f"语料技能≈{n_jobs * 12}")


def bench_cosine(n: int) -> None:
    print("\n--- 余弦相似度（cosine_similarity，稀疏向量）---")
    corpus = make_job_corpus(100)
    jd_w, res_w = compute_tfidf_weights(corpus, make_resume_skills(20))
    # 故意造大向量测规模敏感度
    for dim in (20, 100, 500):
        big = {f"skill_{i}": 0.5 + (i % 7) * 0.1 for i in range(dim)}
        other = {f"skill_{i}": 0.3 + (i % 5) * 0.05 for i in range(0, dim, 2)}
        ts = time_fn(lambda a=big, b=other: cosine_similarity(a, b), n)
        record("cosine", f"vec1_dim={dim:<4} vec2_dim={dim // 2}", ts)

    ts = time_fn(lambda: cosine_similarity(jd_w, res_w), n)
    record("cosine", f"真实权重向量 jd={len(jd_w)} resume={len(res_w)}", ts)


def bench_skill_match(n: int) -> None:
    print("\n--- skill_match_score（TF-IDF + 余弦，完整打分）---")
    for n_jobs in JOB_SIZES:
        corpus = make_job_corpus(n_jobs)
        for n_sk in RESUME_SIZES:
            resume = make_resume_skills(n_sk)
            ts = time_fn(lambda r=resume, c=corpus: skill_match_score(r, c), n)
            record("skill_match", f"jobs={n_jobs:<4} resume_skills={n_sk}", ts)


def bench_combined(n: int) -> None:
    print("\n--- combined_skill_score（TF-IDF 60% + 覆盖率 40%）---")
    for n_jobs in (10, 100, 500):
        corpus = make_job_corpus(n_jobs)
        resume = make_resume_skills(20)
        ts = time_fn(lambda r=resume, c=corpus: combined_skill_score(r, c), n)
        record("combined", f"jobs={n_jobs:<4} resume_skills=20", ts)


def bench_coverage(n: int) -> None:
    print("\n--- skill_coverage_score（集合交并）---")
    resume = set(make_resume_skills(20))
    for n_jobs in (10, 100, 500):
        corpus = make_job_corpus(n_jobs)
        union: set[str] = set()
        for skills in corpus:
            union.update(skills)
        ts = time_fn(lambda r=resume, u=union: skill_coverage_score(r, u), n)
        record("coverage", f"jd_union={len(union):<4} resume=20", ts)


def bench_scaling_curve(n: int) -> None:
    """固定简历 20 技能，扫 JD 数量，观察耗时随语料规模的增长。"""
    print("\n--- 规模扫描：固定 resume=20，扫描 JD 数（TF-IDF 部分）---")
    resume = make_resume_skills(20)
    for n_jobs in [10, 25, 50, 100, 200, 400, 800]:
        corpus = make_job_corpus(n_jobs)
        # 迭代次数随规模下降，避免大语料拖太久
        iters = max(20, n // max(1, n_jobs // 50))
        ts = time_fn(lambda c=corpus, r=resume: compute_tfidf_weights(c, r), iters)
        record("scale", f"jobs={n_jobs}", ts, extra=f"iters={iters}")


def main() -> None:
    ap = argparse.ArgumentParser(description="匹配算法 TF-IDF/余弦性能 benchmark")
    ap.add_argument("--n", type=int, default=100, help="每项迭代次数（默认 100）")
    args = ap.parse_args()
    n = args.n

    print(f"=== 匹配算法 benchmark（n={n}，单位 ms）===")
    print(f"技能词典大小: {len(_SKILL_UNIVERSE)}")
    bench_tfidf(n)
    bench_cosine(n)
    bench_skill_match(n)
    bench_combined(n)
    bench_coverage(n)
    bench_scaling_curve(n)
    print("\n说明：jobs/resume_skills 均为合成语料；scale 段用于观察 TF-IDF 随 JD 数的近似线性增长。")


if __name__ == "__main__":
    main()
