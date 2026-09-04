"""链路 D 端到端演示 + Spark benchmark：
服务端注册 jd_large.csv（20 万行 > 阈值 10 万，遵循文档 §37 大文件不走上传）
→ 登录 → 提问 → Supervisor → DataAgent → AnalysisRouter 自动选 Spark
→ 容器内 spark-submit → 结果回读 → Validator → SSE final。
用法：python scripts/chain_d_demo.py
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8100"
CSV = REPO / "data" / "large" / "jd_large.csv"


def post(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + url, data=data, headers=headers or {})
    return urllib.request.urlopen(req, timeout=120)


def main() -> None:
    # 0. 登录（注册失败说明已存在，直接登录）
    body = json.dumps({"username": "chaind", "password": "chaind123456"}).encode()
    try:
        post("/api/auth/register", body, {"Content-Type": "application/json"})
    except Exception:
        pass
    with post("/api/auth/login", body, {"Content-Type": "application/json"}) as r:
        token = json.load(r)["token"]
    auth = {"Authorization": f"Bearer {token}"}

    # 1. 服务端注册大数据集（不经 HTTP 上传）
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "register_local_dataset.py"), str(CSV), "jd_large"],
        capture_output=True, text=True, check=True,
    )
    ds = json.loads(out.stdout.strip().splitlines()[-1])
    print(f"注册: {ds['rows_estimate']} 行, engine_hint={ds['engine_hint']}, table=ds_{ds['dataset_id'][:8]}")

    # 2. 创建任务（真 GLM 生成问题说明；引擎由行数路由决定走 Spark）
    q = json.dumps({"dataset_id": ds["dataset_id"], "query": "JD 中需求最多的技能 Top 10"},
                   ensure_ascii=False).encode()
    with post("/api/tasks", q, {**auth, "Content-Type": "application/json"}) as r:
        task = json.load(r)
    print("任务:", task["task_id"])

    # 3. 消费 SSE（Spark 容器冷启动 + 作业，预留 6 分钟）
    t0 = time.perf_counter()
    with urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/api/tasks/{task['task_id']}/events", headers=auth), timeout=360
    ) as stream:
        for raw in stream:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                if line.startswith("event: done"):
                    break
                continue
            ev = json.loads(line[5:])
            t = ev.get("type")
            if t == "engine":
                print(f"[engine] {ev['engine']} rows={ev.get('rows_estimate')} reason={ev.get('reason')}")
            elif t == "agent_end":
                print(f"[agent_end] {ev['agent']} {ev.get('latency_ms')}ms {ev.get('status')}")
            elif t == "final":
                res = ev["result"]
                print(f"[final] engine={res['engine']} elapsed={res['elapsed_ms']}ms "
                      f"端到端={(time.perf_counter() - t0):.1f}s rows={res['row_count']}")
                for row in res["rows"]:
                    print(f"  {row}")
            elif t == "error":
                print(f"[error] {ev.get('code')}: {ev.get('message')}")


if __name__ == "__main__":
    sys.exit(main())
