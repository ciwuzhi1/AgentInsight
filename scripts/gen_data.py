"""生成 demo 数据集：
- data/demo/demo_sales.csv   1 万行销售明细（走 DuckDB 即席查询链路）
- data/large/jd_large.csv    20 万行合成 JD（行数超过 SPARK_ROW_THRESHOLD，走 Spark 链路）
用法：python scripts/gen_data.py [jd_rows]
"""
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
random.seed(42)

REGIONS = ["华东", "华北", "华南", "西南", "东北"]
CATEGORIES = {
    "电子": ["无线耳机", "智能手环", "蓝牙音箱", "充电宝", "机械键盘"],
    "家居": ["保温杯", "台灯", "收纳盒", "香薰机"],
    "服饰": ["卫衣", "运动鞋", "帆布包"],
    "食品": ["坚果礼盒", "挂耳咖啡", "牛轧糖"],
}
PRODUCTS = [(p, c) for c, ps in CATEGORIES.items() for p in ps]


def gen_sales(path: Path, rows: int = 10000) -> None:
    start = date(2025, 1, 1)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_date", "region", "product", "category", "sales", "quantity"])
        for _ in range(rows):
            p, c = random.choice(PRODUCTS)
            w.writerow([
                (start + timedelta(days=random.randrange(181))).isoformat(),
                random.choice(REGIONS),
                p,
                c,
                round(random.uniform(50, 5000), 2),
                random.randint(1, 20),
            ])
    print(f"OK {path} ({rows} rows)")


TITLES = [
    "Python 开发工程师", "数据分析师", "大数据开发工程师", "算法工程师",
    "后端开发工程师", "AI 应用工程师", "运维工程师", "测试开发工程师",
    "数据产品经理", "前端开发工程师", "BI 工程师", "爬虫工程师",
]
SKILLS = [
    "Python", "SQL", "Java", "Spark", "Hive", "Docker", "Kubernetes",
    "MySQL", "Redis", "FastAPI", "Linux", "Git", "Pandas", "Excel",
    "Tableau", "机器学习", "React", "Kafka",
]
CITIES = ["北京", "上海", "深圳", "杭州", "成都", "广州"]


def gen_jd(path: Path, rows: int = 200000) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["job_id", "title", "company", "city", "salary_k", "skills", "posted_date"])
        for i in range(rows):
            n = random.randint(3, 6)
            w.writerow([
                f"J{i:07d}",
                random.choice(TITLES),
                f"C{i % 5000:04d}公司",
                random.choice(CITIES),
                random.randint(10, 80),
                ",".join(random.sample(SKILLS, n)),
                f"2025-{random.randint(1, 6):02d}-{random.randint(1, 28):02d}",
            ])
    print(f"OK {path} ({rows} rows)")


if __name__ == "__main__":
    jd_rows = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
    gen_sales(REPO / "data" / "demo" / "demo_sales.csv")
    gen_jd(REPO / "data" / "large" / "jd_large.csv", jd_rows)
