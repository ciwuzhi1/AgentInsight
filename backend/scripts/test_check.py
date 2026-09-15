import requests
import json
import time

BASE = "http://localhost:8100"

# 读取 token
token = open("../data/test_token.txt", encoding="utf-8-sig").read().strip()
headers = {"Authorization": f"Bearer {token}"}

# 读取任务 ID
task = json.load(open("../data/test_task.json"))
task_id = task["task_id"]

print(f"=== 检查任务 {task_id} ===")
time.sleep(3)

r = requests.get(f"{BASE}/api/tasks/{task_id}", headers=headers, timeout=10)
data = r.json()
print(f"状态: {data.get('status')}")
print(f"引擎: {data.get('engine')}")

final = data.get("final_result") or {}
if final:
    print(f"查询: {final.get('query')}")
    print(f"SQL: {final.get('sql')}")
    print(f"行数: {final.get('row_count')}")
    print(f"解释: {final.get('explanation')}")
    if final.get("chart"):
        print(f"图表: {final['chart']}")
else:
    print("无最终结果")

# 检查步骤
steps = data.get("steps") or []
print(f"\n=== 执行步骤 ({len(steps)}) ===")
for s in steps:
    print(f"  {s.get('agent_name')}: {s.get('status')} ({s.get('latency_ms')}ms)")
