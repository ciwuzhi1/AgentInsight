"""评测用例集：100 case 程序化生成，完全确定可复查（CONTRACTS3 §2.1）。

构成：60 nl2sql（模板组合 + 结构性 expect）+ 20 match（手工分数区间）
+ 10 routing（6 data / 3 match / 1 clarify）+ 10 error（全部 graceful）。
顺序固定，case_id 唯一。
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# 与 scripts/gen_data.py 同规格：确定性种子，保证评测/CI 可复现
_DEMO_SEED = 42
_DEMO_ROWS = 10000
_REGIONS = ["华东", "华北", "华南", "西南", "东北"]
_CATEGORIES = {
    "电子": ["无线耳机", "智能手环", "蓝牙音箱", "充电宝", "机械键盘"],
    "家居": ["保温杯", "台灯", "收纳盒", "香薰机"],
    "服饰": ["卫衣", "运动鞋", "帆布包"],
    "食品": ["坚果礼盒", "挂耳咖啡", "牛轧糖"],
}
_PRODUCTS = [(p, c) for c, ps in _CATEGORIES.items() for p in ps]


def default_demo_csv_path() -> Path:
    """仓库内 demo_sales.csv 默认路径。"""
    return Path(__file__).resolve().parents[3] / "data" / "demo" / "demo_sales.csv"


def ensure_demo_sales_csv(path: str | Path | None = None) -> Path:
    """确保 demo_sales.csv 存在；缺失时确定性生成 1 万行销售明细。

    data/ 目录默认不入库（可由 scripts/gen_data.py 再生），CI/单测冷启动
    必须能自举出同一份 fixture，否则 nl2sql / error 用例会因缺文件失败。
    """
    p = Path(path) if path is not None else default_demo_csv_path()
    if p.is_file() and p.stat().st_size > 0:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(_DEMO_SEED)
    start = date(2025, 1, 1)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_date", "region", "product", "category", "sales", "quantity"])
        for _ in range(_DEMO_ROWS):
            prod, cat = rng.choice(_PRODUCTS)
            w.writerow([
                (start + timedelta(days=rng.randrange(181))).isoformat(),
                rng.choice(_REGIONS),
                prod,
                cat,
                round(rng.uniform(50, 5000), 2),
                rng.randint(1, 20),
            ])
    return p


@dataclass
class Case:
    """单个评测用例。"""

    case_id: str
    kind: str          # nl2sql | match | routing | error
    payload: dict


# nl2sql 结构性 expect 的公共部分：评测数据集固定注册为 eval0001（视图 ds_eval0001）
_EVAL_TABLE = "ds_eval0001"


def _sql_expect(
    columns: list[str],
    aggs: list[str] | None = None,
    order_by: bool | None = None,
    limit_max: int | None = None,
    contains: list[str] | None = None,
) -> dict:
    """构造 nl2sql 结构性 expect（对 SQL 做包含/特征断言，不做全文匹配）。"""
    return {
        "tables": [_EVAL_TABLE],
        "columns": columns,
        "aggs": aggs or [],
        "order_by": order_by,
        "limit_max": limit_max,
        "contains": contains or [],
    }


def _nl2sql_cases() -> list[Case]:
    """60 条 nl2sql：约 58% 是 mock 规则可正常生成的句式，其余为 mock
    答不了的复杂问题（走兜底 SQL / guard / 执行报错路径），expect 均为
    理想结构，指标如实反映 mock 能力（接真 key 后的对照基线）。"""
    cases: list[Case] = []

    def add(question: str, expect: dict) -> None:
        cases.append(
            Case(
                case_id=f"nl2sql_{len(cases) + 1:03d}",
                kind="nl2sql",
                payload={"question": question, "dataset": "demo_sales", "expect": expect},
            )
        )

    # ---- 组1（16）：按{维度}统计{指标}，mock 规则全部命中 ----
    dims = [("地区", "region"), ("品类", "category"), ("商品", "product"), ("月份", "order_date")]
    metrics = [
        ("总销售额", "sales", ["SUM"]),
        ("平均销售额", "sales", ["AVG"]),
        ("总销量", "quantity", ["SUM"]),
        ("平均销量", "quantity", ["AVG"]),
    ]
    for dim_cn, dim_col in dims:
        for metric_cn, metric_col, aggs in metrics:
            add(
                f"按{dim_cn}统计{metric_cn}",
                _sql_expect([dim_col, metric_col], aggs, order_by=True, limit_max=1000),
            )

    # ---- 组2（8）：Top N ----
    for n in (3, 5, 10):
        add(
            f"销量 Top {n} 的商品",
            _sql_expect(["product", "quantity"], ["SUM"], order_by=True, limit_max=n),
        )
    for n in (3, 5, 10):
        add(
            f"销售额 Top {n} 的商品",
            _sql_expect(["product", "sales"], ["SUM"], order_by=True, limit_max=n),
        )
    # 组合条件（含 Top 但 mock 忽略 N，只给全量 LIMIT，预期 fail——如实基线）
    add(
        "按地区统计总销售额 Top 3 的地区",
        _sql_expect(["region", "sales"], ["SUM"], order_by=True, limit_max=3),
    )
    add(
        "按品类统计总销量 Top 2 的品类",
        _sql_expect(["category", "quantity"], ["SUM"], order_by=True, limit_max=2),
    )

    # ---- 组3（6）：整体聚合 ----
    for name, col, aggs in (
        ("总销售额", "sales", ["SUM"]),
        ("平均销售额", "sales", ["AVG"]),
        ("总销量", "quantity", ["SUM"]),
        ("平均销量", "quantity", ["AVG"]),
    ):
        add(f"{name}是多少", _sql_expect([col], aggs, order_by=None, limit_max=1))
    # 双指标：mock 只会输出第一个指标，预期 fail
    add(
        "总销售额和总销量是多少",
        _sql_expect(["sales", "quantity"], ["SUM"], order_by=None, limit_max=1),
    )
    add(
        "平均每笔订单的销售额",
        _sql_expect(["sales"], ["AVG"], order_by=None, limit_max=1),
    )

    # ---- 组4（4）：最近 N 条（mock 写死 LIMIT 10，N 不同则如实 fail）----
    for n in (5, 10, 20):
        add(
            f"最近 {n} 条销售记录",
            _sql_expect(["order_date"], aggs=None, order_by=True, limit_max=n),
        )
    add(
        "最近一周的销售额总计",
        _sql_expect(["sales"], ["SUM"], order_by=None, limit_max=1),
    )

    # ---- 组5（26）：复杂/组合条件。其中 7 条 mock 仍可命中，其余走兜底
    #      SELECT * LIMIT 100 或执行报错（列名不存在），feature 如实 fail ----
    add(
        "按时间统计总销售额",
        _sql_expect(["order_date", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "按类别统计平均销量",
        _sql_expect(["category", "quantity"], ["AVG"], order_by=True, limit_max=1000),
    )
    add(
        "按产品统计总销量",
        _sql_expect(["product", "quantity"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "按区域统计总销售额",
        _sql_expect(["region", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "按品类统计销量和销售额",
        _sql_expect(["category", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "销量 Top 1 的商品",
        _sql_expect(["product", "quantity"], ["SUM"], order_by=True, limit_max=1),
    )
    add(
        "按地区统计销售额并用柱状图展示",
        _sql_expect(["region", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    # 以下 19 条为 mock 答不了的复杂问题（预期走兜底/报错路径）
    add(
        "各地区销售额占比",
        _sql_expect(["region", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "2025年上半年各月销售额环比增长率",
        _sql_expect(["order_date", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "各品类各地区的销售额交叉汇总",
        _sql_expect(["category", "region", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "销售额超过5000的订单有多少笔",
        _sql_expect(["sales"], ["COUNT"], order_by=None, limit_max=1),
    )
    add(
        "哪个地区的平均订单金额最高，请给出该地区名称及其金额",
        _sql_expect(["region", "sales"], ["AVG"], order_by=True, limit_max=1),
    )
    add(
        "对比电子品类和服饰品类哪个销量更高",
        _sql_expect(["category", "quantity"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "统计每个月的销售总额并按月份升序排列",
        _sql_expect(["order_date", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "哪位销售代表的业绩最好",
        _sql_expect(["sales"], ["SUM"], order_by=True, limit_max=1),
    )
    add(
        "销售额的方差和标准差",
        _sql_expect(["sales"], ["VARIANCE", "STDDEV"], order_by=None, limit_max=1),
    )
    add(
        "2025年3月华东地区的总销售额",
        _sql_expect(["sales"], ["SUM"], order_by=None, limit_max=1, contains=["华东"]),
    )
    add(
        "每个地区销售额最高的商品是什么",
        _sql_expect(["region", "product", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "同比增长率超过10%的品类",
        _sql_expect(["category", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "按季度统计销售额",
        _sql_expect(["quarter", "sales"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "按星期几统计销量",
        _sql_expect(["weekday", "quantity"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "把数据按销售额从高到低排序并显示前20%",
        _sql_expect(["sales"], aggs=None, order_by=True, limit_max=1000),
    )
    add(
        "统计华南地区2025年第二季度的月度销量趋势",
        _sql_expect(["order_date", "quantity"], ["SUM"], order_by=True, limit_max=1000),
    )
    add(
        "各商品的销售额与销量的相关系数",
        _sql_expect(["product", "sales", "quantity"], ["CORR"], order_by=None, limit_max=1000),
    )
    add(
        "销售额排名前三的地区分别占总销售额的百分比",
        _sql_expect(["region", "sales"], ["SUM"], order_by=True, limit_max=3),
    )
    add(
        "上个月卖得最好的五件商品",
        _sql_expect(["product", "quantity"], ["SUM"], order_by=True, limit_max=5),
    )

    assert len(cases) == 60, f"nl2sql case 数量异常: {len(cases)}"
    return cases


def _jd(
    title: str,
    skills: list[str],
    must_have: list[str] | None = None,
    company: str = "示例科技",
) -> dict:
    """构造 JD 画像条目（job_profile payload 形态）。"""
    return {
        "id": title,
        "title": title,
        "company": company,
        "skills": skills,
        "must_have": must_have or [],
    }


def _match_cases() -> list[Case]:
    """20 条 match：技能重叠度 0%→100% 手工设计，expect 给分数区间与缺口。

    分数 = 0.5*skill + 0.2*project + 0.1*experience + 0.1*education + 0.1*engineering，
    区间为设计值 ±4（打分纯规则、完全确定）。
    """
    jd_backend = _jd(
        "Python 后端工程师",
        ["Python", "SQL", "Docker", "FastAPI", "MySQL", "Git"],
        must_have=["3年 Web 后端开发经验"],
    )
    jd_backend_no_exp = _jd(
        "Python 后端工程师", ["Python", "SQL", "Docker", "FastAPI", "MySQL", "Git"]
    )
    jd_data = _jd("数据分析师", ["SQL", "Excel", "Python"])
    jd_ops = _jd("运维工程师", ["Linux", "Docker", "Kubernetes"])
    jd_fullstack = _jd("全栈工程师", ["JavaScript", "Python", "Kubernetes"], must_have=["2年 前端经验"])
    jd_cn = _jd("后端开发", ["Python", "容器", "SQL"])

    def case(
        idx: int,
        profile: dict,
        jobs: list[dict],
        score_min: int,
        score_max: int,
        gap_contains: list[str],
    ) -> Case:
        return Case(
            case_id=f"match_{idx:03d}",
            kind="match",
            payload={
                "resume": profile,
                "jobs": jobs,
                "expect": {
                    "score_min": score_min,
                    "score_max": score_max,
                    "gap_contains": gap_contains,
                },
            },
        )

    cases = [
        # 1：0% 技能重叠，低学历低年限
        case(
            1,
            {"skills": ["摄影", "写作"], "education": "大专", "experience_years": 1,
             "projects": ["校园摄影作品集网站"]},
            [jd_backend], 17, 25, ["Python", "SQL", "Docker"],
        ),
        # 2：约 17% 重叠（TF-IDF 算法下分数偏高）
        case(
            2,
            {"skills": ["python"], "education": "大专", "experience_years": 1,
             "projects": ["数据分析入门练习"]},
            [jd_backend], 35, 48, ["SQL", "Docker"],
        ),
        # 3：约 33% 重叠，项目命中
        case(
            3,
            {"skills": ["python", "sql"], "education": "大专", "experience_years": 2,
             "projects": ["用 Python 写的爬虫小工具"]},
            [jd_backend], 52, 65, ["Docker", "FastAPI"],
        ),
        # 4：约 50% 重叠，本科
        case(
            4,
            {"skills": ["python", "sql", "docker"], "education": "本科", "experience_years": 2,
             "projects": ["Docker 化的博客系统"]},
            [jd_backend], 62, 75, ["FastAPI", "MySQL", "Git"],
        ),
        # 5：约 67% 重叠，年限达标
        case(
            5,
            {"skills": ["python", "sql", "docker", "mysql"], "education": "本科",
             "experience_years": 3, "projects": ["基于 MySQL 的库存管理系统"]},
            [jd_backend], 70, 82, ["FastAPI", "Git"],
        ),
        # 6：约 83% 重叠
        case(
            6,
            {"skills": ["python", "sql", "docker", "fastapi", "mysql"], "education": "本科",
             "experience_years": 3, "projects": ["FastAPI + MySQL 的订单后台"]},
            [jd_backend], 75, 88, ["Git"],
        ),
        # 7：100% 重叠 + 硕士 + 超额年限 + 工程化技能齐全
        case(
            7,
            {"skills": ["python", "sql", "docker", "fastapi", "mysql", "git", "linux"],
             "education": "硕士", "experience_years": 5,
             "projects": ["FastAPI 微服务 + Docker Compose + MySQL 集群"]},
            [jd_backend], 80, 95, [],
        ),
        # 8：技能全中但应届无项目、大专
        case(
            8,
            {"skills": ["python", "sql", "docker", "fastapi", "mysql", "git"],
             "education": "大专", "experience_years": 1, "projects": []},
            [jd_backend], 50, 65, [],
        ),
        # 9：双 JD 混合重叠
        case(
            9,
            {"skills": ["python", "sql", "excel"], "education": "本科", "experience_years": 2,
             "projects": ["SQL 数据看板"]},
            [jd_backend, jd_data], 60, 75, ["Docker", "Git"],
        ),
        # 10：双 JD 一中一零，工程化强、项目未命中
        case(
            10,
            {"skills": ["docker", "linux", "kubernetes"], "education": "本科",
             "experience_years": 3, "projects": ["K8s 集群运维实践"]},
            [jd_data, jd_ops], 60, 78, ["SQL", "Excel"],
        ),
        # 11：年限超长但技能为零
        case(
            11,
            {"skills": ["管理", "沟通"], "education": "本科", "experience_years": 8,
             "projects": ["跨部门流程管理"]},
            [jd_backend], 25, 38, ["Python", "Docker"],
        ),
        # 12：博士 + 10 年，技能仅 17%
        case(
            12,
            {"skills": ["python"], "education": "博士", "experience_years": 10,
             "projects": ["Python 机器学习平台"]},
            [jd_backend], 52, 68, ["SQL", "Docker"],
        ),
        # 13：同义词归一（js/py/k8s → JavaScript/Python/Kubernetes）
        case(
            13,
            {"skills": ["js", "py", "k8s", "docker"], "education": "本科", "experience_years": 2,
             "projects": ["用 Vue 和 Node 写过小项目"]},
            [jd_fullstack], 62, 78, [],
        ),
        # 14：50% 重叠、无 JD 经验要求、无项目
        case(
            14,
            {"skills": ["python", "sql", "docker"], "education": "本科", "experience_years": 0,
             "projects": []},
            [jd_backend_no_exp], 48, 62, ["FastAPI", "MySQL", "Git"],
        ),
        # 15：纯学历，技能与项目全空
        case(
            15,
            {"skills": [], "education": "硕士", "experience_years": 0, "projects": []},
            [jd_backend_no_exp], 16, 28, ["Python", "SQL"],
        ),
        # 16：工程化技能满分、JD 技能部分命中
        case(
            16,
            {"skills": ["docker", "kubernetes", "git", "linux", "ci/cd"], "education": "大专",
             "experience_years": 4, "projects": ["GitLab CI/CD 流水线建设"]},
            [jd_backend], 55, 70, ["Python", "FastAPI"],
        ),
        # 17：JD 用中文同义词「容器」，简历写 Docker
        case(
            17,
            {"skills": ["docker"], "education": "本科", "experience_years": 2, "projects": []},
            [jd_cn], 40, 55, ["Python", "SQL"],
        ),
        # 18：仅学历短板的资深候选人
        case(
            18,
            {"skills": ["python", "sql", "docker", "fastapi", "mysql", "git", "linux"],
             "education": "大专", "experience_years": 6,
             "projects": ["FastAPI 电商后端，Docker 部署，MySQL 优化"]},
            [jd_backend], 78, 90, [],
        ),
        # 19：岗位列表为空的边界情形
        case(
            19,
            {"skills": ["python"], "education": "本科", "experience_years": 3, "projects": []},
            [], 60, 75, [],
        ),
        # 20：双 JD 双高重叠
        case(
            20,
            {"skills": ["python", "sql", "docker", "fastapi", "mysql", "git", "excel"],
             "education": "硕士", "experience_years": 4,
             "projects": ["FastAPI 服务与 SQL 报表平台"]},
            [jd_backend, jd_data], 85, 98, [],
        ),
    ]
    assert len(cases) == 20
    return cases


def _routing_cases() -> list[Case]:
    """10 条 routing：6 数据问题 → data_agent、3 带 resume 上下文 → match_agent、
    1 无上下文 → clarify（expect_agent=None）。"""
    profile = {
        "skills": ["python", "sql"],
        "education": "本科",
        "experience_years": 3,
        "projects": ["数据分析平台"],
    }
    data_queries = [
        "按地区统计总销售额",
        "销量 Top 5 的商品",
        "各品类的平均销量是多少",
        "统计每个月的销售总额",
        "销售额 Top 10 的商品",
        "最近的销售记录有哪些",
    ]
    match_queries = [
        "帮我把这份简历和这个岗位做匹配",
        "根据简历匹配适合的岗位",
        "分析一下这份简历与 JD 的匹配度",
    ]
    cases = [
        Case(
            case_id=f"routing_{i + 1:03d}",
            kind="routing",
            payload={
                "query": q,
                "context": {"dataset_id": "eval0001"},
                "expect_agent": "data_agent",
            },
        )
        for i, q in enumerate(data_queries)
    ]
    for offset, q in enumerate(match_queries):
        cases.append(
            Case(
                case_id=f"routing_{len(data_queries) + offset + 1:03d}",
                kind="routing",
                payload={
                    "query": q,
                    "context": {"resume": profile},
                    "expect_agent": "match_agent",
                },
            )
        )
    cases.append(
        Case(
            case_id="routing_010",
            kind="routing",
            payload={"query": "你好", "context": {}, "expect_agent": None},
        )
    )
    assert len(cases) == 10
    return cases


def _error_cases(demo_path: str) -> list[Case]:
    """10 条 error：全部 expect graceful（终态 completed/failed_final 且不崩）。

    designed_fail=True 的用例额外断言 failed_final 且 errors 非空。
    """
    ok_ctx = {"dataset": {"name": "demo_sales", "path": demo_path, "table_name": "ds_eval0001"}}
    ghost_ctx = {
        "dataset": {"name": "ghost", "path": "Z:/no/such/__ghost__.csv", "table_name": "ds_ghost01"}
    }
    specs = [
        # (query, dataset_id, context, designed_fail)
        ("DROP TABLE ds_demo", "eval0001", ok_ctx, False),          # 破坏性提问→guard 链路兜底
        ("删除所有销售数据", "eval0001", ok_ctx, False),              # 中文破坏性提问
        ("查询所有数据; DROP TABLE ds_demo", "eval0001", ok_ctx, False),  # 多语句注入
        ("按地区统计总销售额", "eval_nonexist", ghost_ctx, True),     # 不存在的数据集文件
        ("按品类统计总销量", "eval_ghost2", {}, True),                # 缺少数据集元数据
        ("", "", {}, False),                                        # 空 query 无上下文→clarify
        ("", "eval0001", ok_ctx, False),                            # 空 query 有数据集
        ("按地区统计总销售额" * 2000, "eval0001", ok_ctx, False),     # 超长 query
        ("🔥💥 请处理这条异常输入 💥🔥", "eval0001", ok_ctx, False),   # 乱码/emoji
        ("按季度统计销售额", "eval0001", ok_ctx, True),               # mock 生成不存在的列→执行报错
    ]
    return [
        Case(
            case_id=f"error_{i + 1:03d}",
            kind="error",
            payload={
                "query": q,
                "dataset_id": ds,
                "context": ctx,
                "designed_fail": fail,
                "expect": "graceful",
            },
        )
        for i, (q, ds, ctx, fail) in enumerate(specs)
    ]


def load_cases(demo_path: str | None = None) -> list[Case]:
    """加载全部 100 case（顺序固定：nl2sql → match → routing → error）。

    demo_path：demo_sales.csv 绝对路径，缺省按仓库结构推断（error 用例需要）。
    文件缺失时自动确定性生成，保证 CI/冷启动可跑。
    """
    if demo_path is None:
        demo_path = str(ensure_demo_sales_csv())
    else:
        ensure_demo_sales_csv(demo_path)
    cases = _nl2sql_cases() + _match_cases() + _routing_cases() + _error_cases(demo_path)
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)) == 100, "case_id 重复或数量异常"
    return cases
