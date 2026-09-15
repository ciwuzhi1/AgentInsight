"""上下文压缩 benchmark：压缩率 / token 估算精度 / 原始 vs 压缩体积。

用法：python scripts/benchmark_context.py [--n 100]
测量：
- compress_resume / compress_job / compress_schema / compress_result 的压缩率与耗时
- estimate_tokens 与参考 tokenizer（tiktoken cl100k_base，缺失时回退启发式）的偏差
- build_match_context 原始画像 JSON 体积 vs 压缩后 prompt 体积

结果为实测值，不预填。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.context.budget import TokenBudget, estimate_tokens  # noqa: E402
from app.context.builder import build_match_context, build_nl2sql_context  # noqa: E402
from app.context.compressor import (  # noqa: E402
    compress_job,
    compress_result,
    compress_resume,
    compress_schema,
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
    note = f" {extra}" if extra else ""
    print(
        f"[{layer}] {name:<40} n={len(times_ms):<4} "
        f"mean={mean:>9.4f}ms  median={med:>9.4f}ms  p95={p95:>9.4f}ms{note}"
    )


def record_ratio(layer: str, name: str, before: int, after: int, unit: str = "chars") -> None:
    ratio = (after / before * 100) if before else 0.0
    saved = 100.0 - ratio
    print(
        f"[{layer}] {name:<40} {unit}: {before:>8} → {after:>8}  "
        f"保留 {ratio:6.1f}%  节省 {saved:6.1f}%"
    )


# ---------------------------------------------------------------------------
# 合成数据
# ---------------------------------------------------------------------------

_SKILLS = [
    "Python", "SQL", "Java", "JavaScript", "Docker", "Kubernetes", "AWS",
    "Linux", "React", "Spark", "MySQL", "Redis", "FastAPI", "Pandas", "Git",
    "Go", "TypeScript", "Vue", "PostgreSQL", "Spring Boot", "TensorFlow",
    "PyTorch", "机器学习", "深度学习", "NLP", "CI/CD", "Kafka", "Airflow",
]


def make_big_profile(n_skills: int = 40, n_projects: int = 12) -> dict:
    """超出 compress_resume 截断上限的原始画像，用来测压缩率。"""
    return {
        "skills": (_SKILLS * 3)[:n_skills],
        "projects": [
            f"项目{i + 1}：负责数据管道与匹配引擎设计，使用 {_SKILLS[i % len(_SKILLS)]} 等技术栈，"
            f"覆盖采集、清洗、建模、服务化全链路，服务日活用户超 {1000 * (i + 1)} 人。"
            for i in range(n_projects)
        ],
        "education": "本科",
        "experience_years": 5,
        "highlights": [f"亮点{i}" for i in range(8)],
        "raw_text": "原始简历全文" * 200,
        "phone": "13800000000",
        "email": "bench@example.com",
    }


def make_big_job(n_skills: int = 30) -> dict:
    return {
        "id": 1,
        "title": "高级后端工程师",
        "company": "示例科技",
        "must_have": (_SKILLS * 2)[:n_skills],
        "nice_to_have": (_SKILLS[10:] * 2)[:15],
        "skills": (_SKILLS * 2)[:n_skills],
        "description": "岗位描述" * 300,
        "raw_html": "<div>原始 HTML</div>" * 100,
    }


def make_schema(n_cols: int) -> list[dict]:
    return [
        {"name": f"col_{i}", "type": ["INTEGER", "VARCHAR", "DATE", "DOUBLE"][i % 4]}
        for i in range(n_cols)
    ]


def make_rows(n_cols: int, n_rows: int) -> tuple[list[str], list[list]]:
    columns = [f"col_{i}" for i in range(n_cols)]
    rows = [[f"v{r}c{c}" for c in range(n_cols)] for r in range(n_rows)]
    return columns, rows


def d(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


def time_fn(fn, n: int) -> list[float]:
    ts: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return ts


# ---------------------------------------------------------------------------
# 参考 tokenizer
# ---------------------------------------------------------------------------

_ENC = None
_ENC_NAME = "heuristic"
_ENCODER_PROBED = False


def _load_encoder():
    """懒加载 tiktoken；只探测一次，失败后固定走启发式。"""
    global _ENC, _ENC_NAME, _ENCODER_PROBED
    if _ENCODER_PROBED:
        return _ENC
    _ENCODER_PROBED = True
    try:
        import tiktoken

        _ENC = tiktoken.get_encoding("cl100k_base")
        _ENC_NAME = "tiktoken/cl100k_base"
    except Exception:
        _ENC = None
        _ENC_NAME = "heuristic"
    return _ENC


def reference_tokens(text: str) -> int:
    """参考 token 数：优先 tiktoken；缺失时用「中文1字=1，英文词≈1.3」启发式。"""
    enc = _load_encoder()
    if enc is not None:
        return len(enc.encode(text))
    import re

    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    words = len(re.findall(r"[A-Za-z0-9_]+", text))
    other = len(text) - chinese - sum(len(w) for w in re.findall(r"[A-Za-z0-9_]+", text))
    return chinese + int(words * 1.3) + max(0, other // 4)


# ---------------------------------------------------------------------------
# 基准项
# ---------------------------------------------------------------------------


def bench_compress_ratios() -> None:
    print("\n--- 压缩率（JSON 字符数 原始 → 压缩）---")
    profile = make_big_profile()
    compact = compress_resume(profile)
    record_ratio("ratio", "compress_resume", d(profile), d(compact))

    job = make_big_job()
    compact_job = compress_job(job)
    record_ratio("ratio", "compress_job", d(job), d(compact_job))

    schema = make_schema(80)
    schema_str = compress_schema(schema, max_cols=20)
    record_ratio("ratio", "compress_schema(80列→20)", d(schema), len(schema_str))

    cols, rows = make_rows(12, 500)
    result_str = compress_result(cols, rows, max_rows=5)
    raw_result = d({"columns": cols, "rows": rows})
    record_ratio("ratio", "compress_result(500行→5)", raw_result, len(result_str))

    # token 维度
    print("\n--- 压缩率（estimate_tokens）---")
    for label, before, after in [
        ("resume", profile, compact),
        ("job", job, compact_job),
    ]:
        t_before = estimate_tokens(json.dumps(before, ensure_ascii=False))
        t_after = estimate_tokens(json.dumps(after, ensure_ascii=False))
        print(
            f"[ratio] estimate_tokens {label:<12} "
            f"{t_before:>8} → {t_after:>8}  "
            f"保留 {t_after / t_before * 100 if t_before else 0:6.1f}%"
        )


def bench_compress_speed(n: int) -> None:
    print("\n--- 压缩函数耗时 ---")
    profile = make_big_profile()
    job = make_big_job()
    schema = make_schema(80)
    cols, rows = make_rows(12, 500)

    record("speed", "compress_resume", time_fn(lambda: compress_resume(profile), n))
    record("speed", "compress_job", time_fn(lambda: compress_job(job), n))
    record("speed", "compress_schema(80col)",
           time_fn(lambda: compress_schema(schema, max_cols=20), n))
    record("speed", "compress_result(500row)",
           time_fn(lambda: compress_result(cols, rows, max_rows=5), n))


def bench_token_accuracy(n: int) -> None:
    _load_encoder()
    print(f"\n--- estimate_tokens 精度（参考: {_ENC_NAME}）---")
    samples = [
        ("空串", ""),
        ("纯中文短句", "这是一段用于测试 token 估算的中文文本。"),
        ("纯英文短句", "This is a short English sentence for token estimation."),
        ("混合中英", "Python 简历解析 agent 使用 FastAPI 与 DuckDB 做数据分析。"),
        ("JSON 画像", json.dumps(make_big_profile(), ensure_ascii=False)),
        ("长中文", "数据分析平台" * 500),
        ("长英文", ("The quick brown fox jumps over the lazy dog. " * 80)),
    ]
    print(f"{'样本':<14} {'estimate':>10} {'reference':>10} {'偏差%':>8}")
    abs_errs: list[float] = []
    for name, text in samples:
        est = estimate_tokens(text)
        ref = reference_tokens(text)
        err = abs(est - ref) / ref * 100 if ref else 0.0
        abs_errs.append(err)
        print(f"{name:<14} {est:>10} {ref:>10} {err:>7.1f}%")
    mean_err = statistics.fmean(abs_errs)
    print(f"\n平均绝对偏差: {mean_err:.1f}%（启发式参考下仅供参考，非绝对精度）")

    # estimate_tokens 自身耗时（大文本）
    big = json.dumps(make_big_profile(), ensure_ascii=False) * 20
    record("speed", f"estimate_tokens(len={len(big)})",
           time_fn(lambda: estimate_tokens(big), n))


def bench_context_build(n: int) -> None:
    print("\n--- build_match_context：原始 JSON vs 压缩后 prompt ---")
    resume = make_big_profile()
    jobs = [make_big_job(n_skills=20 + i) for i in range(5)]
    raw_json = json.dumps({"resume": resume, "jobs": jobs}, ensure_ascii=False)
    prompt = build_match_context(resume, jobs)

    record_ratio("builder", "match_context", len(raw_json), len(prompt))
    print(
        f"[builder] estimate_tokens: raw={estimate_tokens(raw_json)} "
        f"prompt={estimate_tokens(prompt)}"
    )

    record("speed", "build_match_context(1+5JD)",
           time_fn(lambda: build_match_context(resume, jobs), n))

    schema_str = compress_schema(make_schema(60))
    query = "按地区统计近一年销售额 Top10"
    examples = [
        {"question": "各商品销量", "sql": "SELECT product, SUM(qty) FROM t GROUP BY 1"},
        {"question": "月度趋势", "sql": "SELECT month(d), SUM(sales) FROM t GROUP BY 1"},
    ]
    nl_prompt = build_nl2sql_context(schema_str, query, examples)
    print(
        f"[builder] nl2sql prompt chars={len(nl_prompt)} "
        f"tokens≈{estimate_tokens(nl_prompt)}"
    )
    record("speed", "build_nl2sql_context",
           time_fn(lambda: build_nl2sql_context(schema_str, query, examples), n))


def bench_budget_truncate(n: int) -> None:
    print("\n--- TokenBudget.truncate_input ---")
    budget = TokenBudget(max_input_tokens=2000)
    short = "按地区统计销售额"
    long_text = "数据分析平台与匹配引擎，" * 400  # 远超 2000 tokens

    record("budget", "check_input(短文本)", time_fn(lambda: budget.check_input(short), n))
    record("budget", "check_input(长文本)", time_fn(lambda: budget.check_input(long_text), n))
    record("budget", "truncate_input(长→2000)",
           time_fn(lambda: budget.truncate_input(long_text), n))

    truncated = budget.truncate_input(long_text)
    print(
        f"[budget] 长文本 {len(long_text)} 字符 → 截断后 {len(truncated)} 字符，"
        f"estimate_tokens={estimate_tokens(truncated)} (上限 2000)"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="上下文压缩 / token 估算 benchmark")
    ap.add_argument("--n", type=int, default=100, help="每项耗时测量迭代次数（默认 100）")
    args = ap.parse_args()
    n = args.n

    print(f"=== 上下文工程 benchmark（n={n}）===")
    _load_encoder()
    print(f"token 参考实现: {_ENC_NAME}")
    bench_compress_ratios()
    bench_compress_speed(n)
    bench_token_accuracy(n)
    bench_context_build(n)
    bench_budget_truncate(n)
    print("\n说明：压缩率 = 压缩后/原始；estimate_tokens 精度依赖参考 tokenizer，无 tiktoken 时为启发式对比。")


if __name__ == "__main__":
    main()
