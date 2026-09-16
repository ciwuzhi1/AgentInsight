import json, sys
path = sys.argv[1] if len(sys.argv) > 1 else "data/trace.json"
with open(path, encoding="utf-8-sig") as f:
    d = json.load(f)
print("Status:", d["status"])
print("Query:", d.get("query", ""))
print("Steps:", len(d["steps"]))
for s in d["steps"]:
    lat = s.get("latency_ms") or "-"
    print(f"  {s['step']}. {s['agent_name']}: {s['status']} ({lat}ms)")
