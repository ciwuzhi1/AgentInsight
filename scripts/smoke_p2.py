"""整体简单测试：页面 / 登录 / 数据隔离 / 链路B两次(HIT+409) / 回归外置。"""
import json
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8100"
client = httpx.Client(base_url=BASE, timeout=90)


def register_login(username: str) -> dict:
    r = client.post("/api/auth/register", json={"username": username, "password": "test123456"})
    if r.status_code == 409:
        pass  # 已存在则直接登录
    r = client.post("/api/auth/login", json={"username": username, "password": "test123456"})
    r.raise_for_status()
    return r.json()


def sse_events(task_id: str, token: str, max_s: float = 120) -> list[dict]:
    events = []
    with client.stream("GET", f"/api/tasks/{task_id}/events", headers={"Authorization": f"Bearer {token}"}, timeout=max_s) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
            elif line.startswith("event: done"):
                break
    return events


def main() -> None:
    suffix = uuid.uuid4().hex[:6]
    ua, ub = f"alice_{suffix}", f"bob_{suffix}"

    # 1. 页面与登录
    r1 = httpx.get("http://localhost:3100", timeout=15)
    r2 = httpx.get("http://localhost:3100/settings", timeout=15)
    A = register_login(ua)
    B = register_login(ub)
    ha = {"Authorization": f"Bearer {A['token']}"}
    hb = {"Authorization": f"Bearer {B['token']}"}
    print(f"[1] 页面 home={r1.status_code} settings={r2.status_code} | 登录 A={A['username']} B={B['username']}")

    # 2. 无 token 401
    r = client.post("/api/resumes", files={"file": ("x.txt", b"hi", "text/plain")})
    print(f"[2] 无 token 上传: {r.status_code} (预期 401)")

    # 3. A 上传数据集+简历；B 访问 → 404 隔离
    with open(r"D:/Development/aiproject/AgentInsight/data/demo/demo_sales.csv", "rb") as f:
        ds = client.post("/api/datasets", headers=ha, files={"file": ("demo_sales.csv", f, "text/csv")}).json()
    with open(r"D:/Development/aiproject/AgentInsight/data/demo/sample_resume.txt", "rb") as f:
        res = client.post("/api/resumes", headers=ha, files={"file": ("sample_resume.txt", f, "text/plain")}).json()
    rb = client.get(f"/api/resumes/{res['resume_id']}", headers=hb)
    print(f"[3] 隔离: A上传 ds={ds['dataset_id'][:8]} resume={res['resume_id'][:8]} | B读A的简历 → {rb.status_code} (预期 404)")

    # 4. 链路B 两次（A 的 token）：首次 MISS（真 GLM），运行中重复提交 409，二次 HIT
    jobs = client.get("/api/crawler/jobs?limit=2").json()["items"]
    body = {"resume_id": res["resume_id"], "job_ids": [jobs[0]["id"], jobs[1]["id"]]}

    t0 = time.perf_counter()
    t1 = client.post("/api/matches", headers=ha, json=body).json()["task_id"]
    time.sleep(2)
    dup = client.post("/api/matches", headers=ha, json=body)
    ev1 = sse_events(t1, A["token"])
    d1 = (time.perf_counter() - t0) * 1000
    f1 = next((e for e in ev1 if e.get("type") == "final"), None)
    dupinfo = f"{dup.status_code} task_id同={dup.json().get('task_id') == t1}" if dup.status_code == 409 else str(dup.status_code)
    print(f"[4] 第一次: {d1:.0f}ms 重复提交→{dupinfo} score={f1['result']['score'] if f1 else None} "
          f"cache={[(e['hit'], e['key'][-4:]) for e in ev1 if e.get('type')=='cache']}")

    t0 = time.perf_counter()
    t2 = client.post("/api/matches", headers=ha, json=body).json()["task_id"]
    ev2 = sse_events(t2, A["token"])
    d2 = (time.perf_counter() - t0) * 1000
    f2 = next((e for e in ev2 if e.get("type") == "final"), None)
    hits = [(e["hit"], e["key"][-4:]) for e in ev2 if e.get("type") == "cache"]
    print(f"[5] 第二次: {d2:.0f}ms cache={hits} score={f2['result']['score'] if f2 else None}")
    print(f"[6] 结论: 提速 {d1/max(d2,1):.1f}x | 二次全HIT={all(h for h, _ in hits) if hits else 'N/A'}")


if __name__ == "__main__":
    main()
