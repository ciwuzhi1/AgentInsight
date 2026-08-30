"""后端响应及时性测试：分层用例 + 中位/P95 实测表（结果写 data/bench_report.json）。

用法：python scripts/api_bench.py [--n 10] [--base http://127.0.0.1:8100]
原则：所有数字为实测，不预填；发现不达标项由主线程定位修复后复测。
"""
import argparse
import io
import json
import statistics
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
RESULTS: list[dict] = []


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(len(xs) * p)))
    return xs[k]


def record(layer: str, name: str, times_ms: list[float], extra: str = "") -> None:
    r = {
        "layer": layer,
        "name": name,
        "n": len(times_ms),
        "median_ms": round(statistics.median(times_ms), 1),
        "p95_ms": round(pct(times_ms, 0.95), 1),
        "max_ms": round(max(times_ms), 1),
    }
    if extra:
        r["note"] = extra
    RESULTS.append(r)
    print(f"[{layer}] {name:<38} n={r['n']:<3} 中位={r['median_ms']:>8}ms  P95={r['p95_ms']:>8}ms  max={r['max_ms']:>8}ms {extra}")


def bench_get(client: httpx.Client, layer: str, name: str, path: str, n: int) -> None:
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        resp = client.get(path)
        ts.append((time.perf_counter() - t0) * 1000)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    record(layer, name, ts)


def sse_latency(client: httpx.Client, path: str, timeout: float = 30.0) -> tuple[float, float, dict | None]:
    """返回 (首事件ms, finalms, final_result)。"""
    t0 = time.perf_counter()
    first_ms = final_ms = None
    final_result = None
    with client.stream("GET", path, timeout=timeout) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                if line.startswith("event: done"):
                    break
                continue
            if first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000
            ev = json.loads(line[5:].strip())
            if ev.get("type") == "final":
                final_ms = (time.perf_counter() - t0) * 1000
                final_result = ev.get("result")
    return first_ms or -1, final_ms or -1, final_result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    args = ap.parse_args()
    n = args.n
    client = httpx.Client(base_url=args.base, timeout=30)

    # L1 可用性
    for path, name in [("/api/health", "GET /api/health"),
                       ("/api/health/mysql", "GET /api/health/mysql"),
                       ("/api/health/redis", "GET /api/health/redis")]:
        bench_get(client, "L1-可用性", name, path, n)

    # L2 读接口
    for path, name in [("/api/settings", "GET /api/settings"),
                       ("/api/models", "GET /api/models"),
                       ("/api/crawler/jobs?limit=20", "GET /api/crawler/jobs")]:
        bench_get(client, "L2-读接口", name, path, n)

    # L3a 上传（1万行 CSV）
    ts, ds_id = [], None
    for _ in range(5):
        t0 = time.perf_counter()
        with open(REPO / "data/demo/demo_sales.csv", "rb") as f:
            resp = client.post("/api/datasets", files={"file": ("demo_sales.csv", f, "text/csv")})
        ts.append((time.perf_counter() - t0) * 1000)
        ds_id = resp.json()["dataset_id"]
    record("L3-写与链路", "POST /api/datasets(1万行)", ts)

    # L3b 数据链路：任务创建 + SSE 首事件/final
    ts_first, ts_final = [], []
    for _ in range(5):
        resp = client.post("/api/tasks", json={"dataset_id": ds_id, "query": "按地区统计总销售额"})
        task_id = resp.json()["task_id"]
        first, final, result = sse_latency(client, f"/api/tasks/{task_id}/events")
        ts_first.append(first)
        ts_final.append(final)
        assert result and result.get("row_count", 0) > 0, "数据链路 final 缺结果"
    record("L3-写与链路", "数据链路 SSE 首事件", ts_first)
    record("L3-写与链路", "数据链路 SSE final(端到端)", ts_final, extra="含NL2SQL+DuckDB+Validator")

    # L3c 匹配链路（先确保有简历与岗位）
    resume_path = REPO / "data/demo/sample_resume.txt"
    if not resume_path.exists():
        resume_path.write_text("张三\n本科，5年经验\n技能：Python, JavaScript, Docker, Kubernetes, Linux, MySQL, Git, 机器学习\n项目：多智能体数据分析平台\n", encoding="utf-8")
    with open(resume_path, "rb") as f:
        rid = client.post("/api/resumes", files={"file": ("sample_resume.txt", f, "text/plain")}).json()["resume_id"]
    jobs = client.get("/api/crawler/jobs?limit=2").json()["items"]
    if len(jobs) >= 2:
        ts_first, ts_final = [], []
        job_ids = [jobs[0]["id"], jobs[1]["id"]]
        for _ in range(5):
            resp = client.post("/api/matches", json={"resume_id": rid, "job_ids": job_ids})
            task_id = resp.json()["task_id"]
            first, final, result = sse_latency(client, f"/api/tasks/{task_id}/events")
            ts_first.append(first)
            ts_final.append(final)
            assert result and "score" in result, "匹配链路 final 缺 score"
        record("L3-写与链路", "匹配链路 SSE 首事件", ts_first)
        record("L3-写与链路", "匹配链路 SSE final(端到端)", ts_final, extra="resume∥job并行+规则打分")
    else:
        print("[L3] 岗位库为空，跳过匹配链路（先在爬虫面板抓取）")

    # L4 健壮性回归
    t0 = time.perf_counter()
    big = io.BytesIO(b"x" * (11 * 1024 * 1024))
    resp = client.post("/api/datasets", files={"file": ("big.csv", big, "text/csv")})
    assert resp.status_code == 413, f"预期 413，实际 {resp.status_code}"
    record("L4-健壮性", "11MB 上传 → 413 拒绝", [(time.perf_counter() - t0) * 1000])

    resp = client.post("/api/tasks", json={"dataset_id": ds_id, "query": "DROP TABLE test"})
    task_id = resp.json().get("task_id")
    _, _, result = sse_latency(client, f"/api/tasks/{task_id}/events") if task_id else (0, 0, None)
    ok_guard = task_id is None or (result is None)  # 危险 SQL 不产出结果
    record("L4-健壮性", "DROP 注入 → 被拒(无结果)", [0.0 if ok_guard else 1.0], extra="通过" if ok_guard else "未通过!")

    (out := REPO / "data/bench_report.json").write_text(
        json.dumps({"base": args.base, "results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out}")
    client.close()


if __name__ == "__main__":
    main()
