import requests
import json

BASE = "http://localhost:8100"

token = open("../data/test_token.txt", encoding="utf-8-sig").read().strip()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

print("=== 测试简历上传 ===")
with open("../data/demo/sample_resume.txt", "rb") as f:
    r = requests.post(
        f"{BASE}/api/resumes",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("resume.txt", f, "text/plain")},
        timeout=30,
    )
print(f"状态: {r.status_code}")
resume_data = r.json()
print(f"简历 ID: {resume_data.get('resume_id')}")
profile = resume_data.get("profile") or {}
print(f"技能: {profile.get('skills', [])[:5]}")
print(f"学历: {profile.get('education')}")
print(f"年限: {profile.get('experience_years')}")

print("\n=== 测试匹配任务 ===")
body = {"resume_id": resume_data.get("resume_id"), "job_ids": [1, 2, 3]}
r = requests.post(f"{BASE}/api/matches", headers=headers, json=body, timeout=10)
print(f"状态: {r.status_code}")
print(f"响应: {r.text[:200]}")
match_task = r.json()

if match_task.get("task_id"):
    import time
    time.sleep(5)
    r = requests.get(f"{BASE}/api/tasks/{match_task['task_id']}", headers=headers, timeout=10)
    data = r.json()
    print(f"\n匹配状态: {data.get('status')}")
    final = data.get("final_result") or {}
    if final.get("score") is not None:
        print(f"匹配分数: {final.get('score')}")
        print(f"维度: {final.get('dimensions')}")
        print(f"技能缺口: {final.get('skill_gap', [])[:5]}")
        print(f"解读: {final.get('interpretation', '')[:100]}")
