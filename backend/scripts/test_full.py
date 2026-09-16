import requests
import json
import time

BASE = "http://localhost:8100"
token = open("../data/test_token.txt", encoding="utf-8-sig").read().strip()
headers = {"Authorization": f"Bearer {token}"}

# 检查匹配任务
task_id = "faaa2dd2-803d-4d08-998c-356f3c30790f"
print(f"=== 检查匹配任务 {task_id} ===")
time.sleep(3)

r = requests.get(f"{BASE}/api/tasks/{task_id}", headers=headers, timeout=10)
data = r.json()
print(f"状态: {data.get('status')}")

final = data.get("final_result") or {}
if final.get("score") is not None:
    print(f"匹配分数: {final.get('score')}")
    print(f"维度: {final.get('dimensions')}")
    print(f"技能缺口: {final.get('skill_gap', [])[:5]}")
    print(f"解读: {final.get('interpretation', '')[:100]}")
else:
    print(f"最终结果: {json.dumps(final, ensure_ascii=False)[:300]}")

# 检查步骤
steps = data.get("steps") or []
print(f"\n=== 执行步骤 ({len(steps)}) ===")
for s in steps:
    print(f"  {s.get('agent_name')}: {s.get('status')} ({s.get('latency_ms')}ms)")

# 测试 Few-shot 检索
print("\n=== 测试 Few-shot 检索 ===")
from app.agents.few_shot import add_to_history, retrieve_similar, _tokenize

# 添加历史
add_to_history("按地区统计总销售额", "SELECT region, SUM(sales) FROM t GROUP BY region", "mock")
add_to_history("统计各城市岗位数量", "SELECT city, COUNT(*) FROM jobs GROUP BY city", "mock")

# 测试检索
results = retrieve_similar("按地区汇总销售额")
print(f"检索到 {len(results)} 条相似历史")
for r in results:
    print(f"  问题: {r['question']}")
    print(f"  SQL: {r['sql']}")

# 测试分词
tokens = _tokenize("按地区统计总销售额")
print(f"\n分词结果: {tokens[:10]}")
