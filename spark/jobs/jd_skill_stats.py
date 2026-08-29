# -*- coding: utf-8 -*-
"""岗位技能统计 Spark 任务（容器内独立运行，不 import app.*）。

用法:
    spark-submit /jobs/jd_skill_stats.py --input /data/large/jd_large.csv --output /data/out/jd_stats_xxx

逻辑: 读 CSV -> skills 列按逗号 explode -> trim+lower 标准化 -> 过滤空串
      -> groupBy 计数 -> count 降序 -> coalesce(1) 单文件 CSV 输出。
另在 output 的上一级目录写 summary.json（输入总行数、耗时）。
"""

import argparse
import json
import os
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def parse_args():
    parser = argparse.ArgumentParser(description="岗位技能出现次数统计")
    parser.add_argument("--input", required=True, help="输入 CSV 路径（容器内）")
    parser.add_argument("--output", required=True, help="输出目录（容器内）")
    return parser.parse_args()


def main():
    args = parse_args()
    spark = SparkSession.builder.appName("jd_skill_stats").getOrCreate()
    start = time.time()
    print(f"[jd_skill_stats] 读取输入: {args.input}", flush=True)

    df = spark.read.csv(args.input, header=True, inferSchema=True)
    input_rows = df.count()
    print(f"[jd_skill_stats] 输入行数: {input_rows}", flush=True)

    # skills 按逗号拆成多行；split/explode 对 null 列自动跳过
    exploded = (
        df.select(F.explode(F.split(F.col("skills"), ",")).alias("skill"))
        .withColumn("skill", F.lower(F.trim(F.col("skill"))))
        .filter(F.col("skill").isNotNull() & (F.col("skill") != ""))
    )
    stats = exploded.groupBy("skill").count().orderBy(F.desc("count"))

    stats.coalesce(1).write.mode("overwrite").option("header", True).csv(args.output)
    print(f"[jd_skill_stats] 结果已写入: {args.output}", flush=True)

    elapsed_ms = int((time.time() - start) * 1000)
    # summary 写到 output 的上一级目录（driver 端标准库即可，无需 Spark）
    output_norm = args.output.rstrip("/")
    summary_path = os.path.join(os.path.dirname(output_norm), "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"total_rows": input_rows, "elapsed_ms": elapsed_ms}, f, ensure_ascii=False)
    print(f"[jd_skill_stats] 完成，耗时 {elapsed_ms}ms，summary: {summary_path}", flush=True)

    spark.stop()


if __name__ == "__main__":
    main()
