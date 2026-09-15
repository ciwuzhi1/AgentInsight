"""Agent 执行耗时 benchmark：各 agent 类型端到端 run() + 有/无缓存对比。

用法：python scripts/benchmark_agents.py [--n 100]
说明：
- 离线可跑：Redis/MySQL/真实 LLM 均被替换为内存 mock，测的是 agent 自身逻辑开销。
- resume/job agent 分别测 cache MISS（解析+写缓存）与 cache HIT（直接读缓存）。
- 计时用 time.perf_counter()，输出 均值/中位/P95 表。

结果为实测值，不预填。
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

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
        f"[{layer}] {name:<36} n={len(times_ms):<4} "
        f"mean={mean:>9.3f}ms  median={med:>9.3f}ms  p95={p95:>9.3f}ms  max={mx:>9.3f}ms{note}"
    )


# ---------------------------------------------------------------------------
# 离线 mock：内存缓存 / 假 LLM / 跳过落库与设置中心
# ---------------------------------------------------------------------------

_MEM_CACHE: dict[str, dict] = {}


async def _mem_get_json(key: str) -> dict | None:
    return _MEM_CACHE.get(key)


async def _mem_set_json(key: str, obj: dict, ttl: int) -> bool:
    _MEM_CACHE[key] = obj
    return True


def _noop_save_resume(*_a, **_k) -> None:
    return None


async def _setting_no_db(key: str, default: str) -> str:
    from app.core.app_settings import DEFAULT_SETTINGS

    return DEFAULT_SETTINGS.get(key, default)


def install_offline_mocks() -> None:
    """把 Redis / MySQL / 设置中心 / LLM 换成离线实现，保证 benchmark 稳定可复现。"""
    import app.agents.job_agent as job_mod
    import app.agents.match_agent as match_mod
    import app.agents.report_agent as report_mod
    import app.agents.resume_agent as resume_mod
    import app.core.llm as llm_mod
    import app.persistence.mysql as mysql_mod

    # 内存缓存替代 Redis（get_json/set_json 均为 async）
    for mod in (resume_mod, job_mod):
        mod.get_json = _mem_get_json
        mod.set_json = _mem_set_json
        mod.get_setting_safe = _setting_no_db

    match_mod.get_setting_safe = _setting_no_db
    report_mod.get_setting_safe = _setting_no_db

    # 强制 Mock LLM（is_mock=True → agent 走规则路径，无网络）
    llm_mod.get_llm_client = lambda: llm_mod.MockLLMClient()

    # 落库 no-op
    mysql_mod.save_resume = _noop_save_resume


# ---------------------------------------------------------------------------
# 合成数据
# ---------------------------------------------------------------------------

_RESUME_TEMPLATE = """张三
{years} 年工作经验 | {edu}
技能：{skills}
项目经历：
- 多智能体数据分析平台（Python, FastAPI, DuckDB, Redis）
- 招聘匹配引擎（TF-IDF, 机器学习, Kubernetes）
- 实时数仓 ETL（Spark, MySQL, Docker）
自我评价：熟悉工程化与性能优化，能独立交付端到端系统。
"""

_SKILL_POOL = [
    "Python", "SQL", "Java", "JavaScript", "Docker", "Kubernetes", "AWS",
    "Linux", "React", "Spark", "MySQL", "Redis", "FastAPI", "CSS", "HTML",
    "Pandas", "Git", "Go", "TypeScript", "Vue", "PostgreSQL", "Spring Boot",
    "TensorFlow", "PyTorch", "机器学习", "深度学习", "NLP", "CI/CD",
]


def make_resume_text(i: int) -> str:
    skills = ", ".join(_SKILL_POOL[i % len(_SKILL_POOL):] + _SKILL_POOL[: i % len(_SKILL_POOL)])
    return _RESUME_TEMPLATE.format(years=3 + i % 8, edu="本科" if i % 2 else "硕士", skills=skills)


def make_jobs(n_jobs: int = 3) -> list[dict]:
    jobs = []
    for i in range(n_jobs):
        jobs.append({
            "id": i + 1,
            "title": f"后端工程师{i + 1}",
            "company": f"示例科技{i + 1}",
            "skills": ",".join(_SKILL_POOL[i:i + 12]),
            "description": "要求熟悉 Python、Docker、Kubernetes、MySQL，有数据分析平台经验优先。"
            f" 团队 {i + 1} 号岗位。",
        })
    return jobs


def make_match_payload(i: int) -> tuple[dict, list[dict]]:
    profile = {
        "skills": _SKILL_POOL[i % 5: i % 5 + 15],
        "projects": ["多智能体数据分析平台", "招聘匹配引擎"],
        "education": "本科",
        "experience_years": 3 + i % 8,
    }
    jobs = [
        {
            "id": j + 1,
            "title": f"岗位{j + 1}",
            "skills": _SKILL_POOL[j:j + 10],
            "must_have": _SKILL_POOL[j + 2: j + 8],
        }
        for j in range(3)
    ]
    return profile, jobs


async def _noop_emit(_event: dict) -> None:
    return None


# ---------------------------------------------------------------------------
# 各 agent 基准
# ---------------------------------------------------------------------------


async def bench_resume_agent(n: int) -> None:
    from app.agents.resume_agent import ResumeAgent
    from app.agent_runtime.state import TaskState

    agent = ResumeAgent()
    tmp = Path(tempfile.mkdtemp(prefix="bench_resume_"))

    # MISS：每次新文件 → 未命中缓存
    ts: list[float] = []
    for i in range(n):
        path = tmp / f"resume_miss_{i}.txt"
        path.write_text(make_resume_text(i), encoding="utf-8")
        state = TaskState()
        state.context["resume"] = {
            "path": str(path),
            "resume_id": f"bench-miss-{i}",
            "filename": path.name,
        }
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok", f"resume MISS 失败: {result.errors}"
        assert result.data.get("cache") == "miss"
    record("resume_agent", "run() cache MISS", ts, extra="文件hash+规则画像+写缓存")

    # HIT：同一文件重复跑
    hit_path = tmp / "resume_hit.txt"
    hit_path.write_text(make_resume_text(999), encoding="utf-8")
    warm = TaskState()
    warm.context["resume"] = {
        "path": str(hit_path),
        "resume_id": "bench-hit-warm",
        "filename": hit_path.name,
    }
    warm_res = await agent.run(warm, _noop_emit)
    assert warm_res.status == "ok"

    ts = []
    for i in range(n):
        state = TaskState()
        state.context["resume"] = {
            "path": str(hit_path),
            "resume_id": f"bench-hit-{i}",
            "filename": hit_path.name,
        }
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok" and result.data.get("cache") == "hit"
    record("resume_agent", "run() cache HIT", ts, extra="文件hash+读缓存")


async def bench_job_agent(n: int) -> None:
    from app.agents.job_agent import JobAgent
    from app.agent_runtime.state import TaskState

    agent = JobAgent()

    ts: list[float] = []
    for i in range(n):
        jobs = make_jobs(3)
        # 每次改 description 保证 MISS
        for j, job in enumerate(jobs):
            job["description"] += f" bench={i}-{j}"
        state = TaskState()
        state.context["jobs"] = jobs
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok" and result.data.get("cache") == "miss"
    record("job_agent", "run() cache MISS", ts, extra="规则画像+写缓存")

    # HIT：固定 jobs 预热后重复
    fixed = make_jobs(3)
    warm = TaskState()
    warm.context["jobs"] = fixed
    warm_res = await agent.run(warm, _noop_emit)
    assert warm_res.status == "ok" and warm_res.data.get("cache") == "miss"

    ts = []
    for i in range(n):
        state = TaskState()
        state.context["jobs"] = [dict(j) for j in fixed]
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok" and result.data.get("cache") == "hit"
    record("job_agent", "run() cache HIT", ts, extra="读缓存")


async def bench_match_agent(n: int) -> None:
    from app.agents.match_agent import MatchAgent
    from app.agent_runtime.state import TaskState

    agent = MatchAgent()
    ts: list[float] = []
    for i in range(n):
        profile, jobs = make_match_payload(i)
        state = TaskState()
        state.results["resume_agent"] = {
            "resume_id": f"r{i}",
            "filename": "bench.txt",
            "profile": profile,
        }
        state.results["job_agent"] = {"jobs": jobs}
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok" and "score" in result.data
    record("match_agent", "run() TF-IDF打分", ts, extra="规则路径(mock LLM)")


async def bench_validator_agent(n: int) -> None:
    from app.agents.validator_agent import ValidatorAgent
    from app.agent_runtime.state import TaskState

    agent = ValidatorAgent()
    profile, jobs = make_match_payload(0)
    match_data = {
        "score": 72,
        "dimensions": {"skill": 80, "project": 100, "experience": 100, "education": 80, "engineering": 40},
        "skill_gap": ["go", "vue"],
        "interpretation": "综合匹配尚可，建议补齐缺口技能。",
        "resume": {"resume_id": "r", "filename": "f", "skills": profile["skills"]},
        "jobs": [{"id": 1, "title": "岗位1", "company": "c"}],
    }
    ts: list[float] = []
    for _ in range(n):
        state = TaskState()
        state.results["match_agent"] = match_data
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok"
    record("validator_agent", "run() 匹配结构校验", ts)


async def bench_report_agent(n: int) -> None:
    from app.agents.report_agent import ReportSynthesizer
    from app.agent_runtime.state import TaskState

    agent = ReportSynthesizer()
    match_data = {
        "score": 72,
        "dimensions": {"skill": 80, "project": 100, "experience": 100, "education": 80, "engineering": 40},
        "skill_gap": ["go", "vue", "rust"],
        "interpretation": "综合匹配尚可。",
    }
    ts: list[float] = []
    for _ in range(n):
        state = TaskState()
        state.results["match_agent"] = match_data
        t0 = time.perf_counter()
        result = await agent.run(state, _noop_emit)
        ts.append((time.perf_counter() - t0) * 1000)
        assert result.status == "ok"
    record("report_agent", "run() 模板报告", ts, extra="report_llm_enabled=false")


async def bench_few_shot(n: int) -> None:
    from app.agents import few_shot

    # 填满历史库（上限 500）
    few_shot._history.clear()
    templates = [
        ("按地区统计总销售额", "SELECT region, SUM(sales) FROM ds GROUP BY region"),
        ("各商品销量 Top10", "SELECT product, SUM(quantity) FROM ds GROUP BY product"),
        ("最近一个月的订单", "SELECT * FROM ds WHERE order_date >= current_date - 30"),
        ("华东地区平均客单价", "SELECT AVG(amount) FROM ds WHERE region='华东'"),
        ("按月份统计销售额趋势", "SELECT month(order_date), SUM(sales) FROM ds GROUP BY 1"),
    ]
    for i in range(200):
        q, s = templates[i % len(templates)]
        few_shot._history.append({"question": f"{q} #{i}", "sql": s, "explanation": ""})

    queries = [
        "帮我统计一下各个地区的销售总额",
        "销量最高的十个商品是什么",
        "看一下华东的平均订单金额",
    ]
    ts: list[float] = []
    for i in range(n):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        hits = few_shot.retrieve_similar(q, top_k=3)
        ts.append((time.perf_counter() - t0) * 1000)
        assert isinstance(hits, list)
    record("few_shot", "retrieve_similar(history=200)", ts)


async def bench_pure_compute(n: int) -> None:
    """纯函数层：不进 agent 状态机，隔离算法本身开销。"""
    from app.agents.match_agent import compute_match, normalize_skill
    from app.agents.resume_agent import _rule_profile
    from app.agents.job_agent import _mock_job

    text = make_resume_text(0)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        _rule_profile(text)
        ts.append((time.perf_counter() - t0) * 1000)
    record("pure", "resume _rule_profile", ts)

    job = make_jobs(1)[0]
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        _mock_job(dict(job))
        ts.append((time.perf_counter() - t0) * 1000)
    record("pure", "job _mock_job", ts)

    profile, jobs = make_match_payload(0)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        compute_match(profile, jobs)
        ts.append((time.perf_counter() - t0) * 1000)
    record("pure", "match compute_match", ts)

    ts = []
    for i in range(n):
        t0 = time.perf_counter()
        normalize_skill(_SKILL_POOL[i % len(_SKILL_POOL)])
        ts.append((time.perf_counter() - t0) * 1000)
    record("pure", "normalize_skill", ts, extra="微秒级，含循环开销")

    # data_agent 的图表推断（纯函数，不依赖引擎）
    try:
        from app.agents.data_agent import build_chart

        columns = ["region", "sales", "quantity", "profit"]
        rows = [[f"地区{i % 29}", float(i), i, float(i) * 0.2] for i in range(200)]
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            build_chart(columns, rows)
            ts.append((time.perf_counter() - t0) * 1000)
        record("pure", "data build_chart(200行)", ts)
    except Exception as exc:  # noqa: BLE001 - duckdb/spark 依赖缺失时跳过
        print(f"[pure] data build_chart 跳过: {exc}")


async def run_all(n: int) -> None:
    install_offline_mocks()
    print(f"=== Agent 执行 benchmark（n={n}，离线 mock，单位 ms）===")
    await bench_pure_compute(n)
    await bench_resume_agent(n)
    await bench_job_agent(n)
    await bench_match_agent(n)
    await bench_validator_agent(n)
    await bench_report_agent(n)
    await bench_few_shot(n)
    print("\n说明：cache HIT vs MISS 差值 ≈ 解析/画像构建成本；pure 层为算法裸耗时。")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent 执行耗时 benchmark")
    ap.add_argument("--n", type=int, default=100, help="每项迭代次数（默认 100）")
    args = ap.parse_args()
    asyncio.run(run_all(args.n))


if __name__ == "__main__":
    main()
