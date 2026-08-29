"""DuckDB 侧 benchmark：对 jd_large.csv 做技能统计聚合（与 Spark job 同一计算语义）。"""
import asyncio
import sys
import time

sys.path.insert(0, "backend")
from app.data_engine.duckdb_engine import duckdb_engine

SQL = """
SELECT lower(trim(sk)) AS skill, COUNT(*) AS cnt
FROM (SELECT unnest(string_split(skills, ',')) AS sk FROM ds_bench01)
WHERE sk != ''
GROUP BY 1 ORDER BY cnt DESC LIMIT 10
"""


def main() -> None:
    t0 = time.perf_counter()
    duckdb_engine.register_dataset(
        "bench01", "jd_large", "D:/Development/aiproject/AgentInsight/data/large/jd_large.csv"
    )
    t1 = time.perf_counter()
    print(f"register: {(t1 - t0) * 1000:.0f} ms")
    r = asyncio.run(duckdb_engine.execute("bench01", SQL))
    print(f"query: engine={r.elapsed_ms} ms, rows={r.row_count}, truncated={r.truncated}")
    for row in r.rows:
        print(row)


if __name__ == "__main__":
    main()
