"""服务端注册大数据集（不经 HTTP 上传，遵循设计文档 §37：大文件放存储而非上传）。

用法：python scripts/register_local_dataset.py <csv路径> [名称]
输出 dataset_id（JSON），供 chain_d_demo 等脚本创建任务。
"""
import json
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.data_engine.duckdb_engine import duckdb_engine  # noqa: E402
from app.data_engine.profiler import profile_csv  # noqa: E402
from app.data_engine.router import choose_engine  # noqa: E402
from app.persistence.mysql import insert_dataset  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python scripts/register_local_dataset.py <csv路径> [名称]")
        sys.exit(1)
    csv_path = Path(sys.argv[1]).resolve()
    if not csv_path.exists():
        print(json.dumps({"error": f"文件不存在: {csv_path}"}))
        sys.exit(1)
    name = sys.argv[2] if len(sys.argv) > 2 else csv_path.stem
    dataset_id = str(uuid.uuid4())
    profile = profile_csv(str(csv_path))
    schema = duckdb_engine.register_dataset(dataset_id, name, str(csv_path))
    try:
        insert_dataset(dataset_id, name, str(csv_path), profile.rows_estimate, profile.size_bytes, schema)
    except Exception as exc:  # 落库失败不阻断（公共遗留数据，无归属）
        print(f"warn: 落库失败（不影响分析）: {exc}", file=sys.stderr)
    print(json.dumps({
        "dataset_id": dataset_id,
        "name": name,
        "rows_estimate": profile.rows_estimate,
        "size_mb": profile.size_mb,
        "engine_hint": choose_engine(profile),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
