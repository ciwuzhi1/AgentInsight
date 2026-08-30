"""链路 D 端到端演示 + Spark benchmark：
上传 jd_large.csv（20 万行 > 阈值 10 万）→ 提问 → Supervisor → DataAgent
→ AnalysisRouter 自动选 Spark → 容器内 spark-submit → 结果回读 → Validator。
用法：python scripts/chain_d_demo.py
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8100"


def post(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + url, data=data, headers=headers or {})
    return urllib.request.urlopen(req, timeout=120)


def main() -> None:
    # 1. 上传 20 万行 JD 数据集
    boundary = "----agentinsight"
    payload = open("data/large/jd_large.csv", "rb").read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="jd_large.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    t0 = time.perf_counter()
    with post("/api/datasets", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}) as r:
        ds = json.load(r)
    print(f"上传: {ds['rows_estimate']} 行, engine_hint={ds['engine_hint']}, table={ds['table_name']} "
          f"({time.perf_counter() - t0:.1f}s)")

    # 2. 创建任务
    q = json.dumps({"dataset_id": ds["dataset_id"], "query": "JD 中需求最多的技能 Top 10"},
                   ensure_ascii=False).encode()
    with post("/api/tasks", q, {"Content-Type": "application/json"}) as r:
        task = json.load(r)
    print("任务:", task["task_id"])

    # 3. 消费 SSE
    with urllib.request.urlopen(f"{BASE}/api/tasks/{task['task_id']}/events", timeout=600) as stream:
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
                print(f"[final] engine={res['engine']} elapsed={res['elapsed_ms']}ms rows={res['row_count']}")
                print(f"  columns: {res['columns']}")
                for row in res["rows"]:
                    print(f"  {row}")
            elif t == "error":
                print(f"[error] {ev.get('code')}: {ev.get('message')}")


if __name__ == "__main__":
    sys.exit(main())
