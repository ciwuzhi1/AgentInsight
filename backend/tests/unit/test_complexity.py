"""complexity 单测：复杂度评估 + 自适应 build_data_plan。"""
from app.agent_runtime.complexity import COMPLEX, MEDIUM, NORMAL, SIMPLE, assess_complexity
from app.agent_runtime.planner import build_data_plan, validate_dag


class TestAssessComplexity:
    def test_simple_query(self):
        assert assess_complexity("统计总销售额") == SIMPLE

    def test_normal_query(self):
        assert assess_complexity("按地区统计销售额并排序") == NORMAL

    def test_complex_query(self):
        assert assess_complexity("对比北京和上海的岗位数量差异") == COMPLEX

    def test_complex_join_keyword(self):
        assert assess_complexity("join users 和 orders 表") == COMPLEX

    def test_complex_period_keyword(self):
        assert assess_complexity("计算本月环比增长") == COMPLEX

    def test_long_query_with_simple_word_is_normal(self):
        # 超过 20 字，即使含聚合词也走 NORMAL
        assert assess_complexity("请帮我统计一下所有销售人员在今年度的总销售额是多少呢") == NORMAL

    def test_empty_query_is_normal(self):
        assert assess_complexity("") == NORMAL

    # ---- MEDIUM 级别 ----

    def test_medium_multiple_aggregations(self):
        # 两个聚合词（总 + 平均）→ MEDIUM
        assert assess_complexity("统计总销售额和平均利润") == MEDIUM

    def test_medium_with_filter_word(self):
        # 含筛选条件词 "其中" → MEDIUM
        assert assess_complexity("统计销售额其中地区为北京") == MEDIUM

    def test_medium_filter_only_no_aggregation(self):
        # 仅含筛选词、无聚合词 → MEDIUM
        assert assess_complexity("筛选出北京地区的数据") == MEDIUM

    def test_medium_filter_word_jinzhi(self):
        # 含 "仅" 筛选词 → MEDIUM
        assert assess_complexity("仅保留上海地区的记录") == MEDIUM

    # ---- 边界用例：SIMPLE / MEDIUM / NORMAL 交界 ----

    def test_boundary_simple_single_aggregation(self):
        # 单聚合 + 短问题 + 无筛选 → SIMPLE（非 MEDIUM）
        assert assess_complexity("统计总销售额") == SIMPLE

    def test_boundary_medium_two_aggregations_short(self):
        # 两个聚合词，即使很短 → MEDIUM（非 SIMPLE）
        assert assess_complexity("总和与平均值") == MEDIUM

    def test_boundary_simple_with_filter_becomes_medium(self):
        # 单聚合 + 筛选词 → MEDIUM（非 SIMPLE）
        assert assess_complexity("统计总销售额其中北京") == MEDIUM

    def test_boundary_normal_no_aggregation_no_filter(self):
        # 无聚合词、无筛选词 → NORMAL
        assert assess_complexity("按地区统计销售额并排序") == NORMAL

    def test_boundary_medium_vs_complex_complex_wins(self):
        # 同时含筛选词和复杂词 → COMPLEX（优先级最高）
        assert assess_complexity("对比北京和上海的数据其中筛选销售额") == COMPLEX

    def test_boundary_long_query_two_aggregations_is_medium(self):
        # 超过 20 字但含两个聚合词 → MEDIUM（非 NORMAL）
        assert assess_complexity("请帮我统计一下所有销售人员在今年度的总销售额和平均工资是多少呢") == MEDIUM


class TestBuildDataPlanAdaptive:
    def test_simple_query_returns_one_step(self):
        steps = build_data_plan("统计总销售额")
        assert [s.id for s in steps] == ["data"]
        assert steps[0].agent == "data_agent"
        # 拓扑校验通过
        ordered = validate_dag(steps)
        assert [s.id for s in ordered] == ["data"]

    def test_normal_query_returns_three_steps(self):
        steps = build_data_plan("按地区统计销售额并排序")
        assert [s.id for s in steps] == ["data", "validator", "report"]
        assert steps[1].depends_on == ["data"]
        assert steps[2].depends_on == ["validator"]

    def test_medium_query_returns_three_steps(self):
        # MEDIUM 暂同 NORMAL：data → validator → report
        steps = build_data_plan("统计总销售额和平均利润")
        assert [s.id for s in steps] == ["data", "validator", "report"]
        assert steps[1].depends_on == ["data"]
        assert steps[2].depends_on == ["validator"]

    def test_medium_filter_query_returns_three_steps(self):
        steps = build_data_plan("筛选出北京地区的数据")
        assert [s.id for s in steps] == ["data", "validator", "report"]

    def test_complex_query_returns_three_steps(self):
        steps = build_data_plan("对比北京和上海的岗位数量差异")
        assert [s.id for s in steps] == ["data", "validator", "report"]

    def test_default_no_query_returns_three_steps(self):
        steps = build_data_plan()
        assert [s.id for s in steps] == ["data", "validator", "report"]
