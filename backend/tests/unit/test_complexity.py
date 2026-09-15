"""complexity 单测：复杂度评估 + 自适应 build_data_plan。"""
from app.agent_runtime.complexity import COMPLEX, NORMAL, SIMPLE, assess_complexity
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


class TestBuildDataPlanAdaptive:
    def test_simple_query_returns_one_step(self):
        steps = build_data_plan("统计总销售额")
        assert [s.id for s in steps] == ["data"]
        assert steps[0].agent == "data_agent"
        # 拓扑校验通过
        ordered = validate_dag(steps)
        assert [s.id for s in ordered] == ["data"]

    def test_normal_query_returns_two_steps(self):
        steps = build_data_plan("按地区统计销售额并排序")
        assert [s.id for s in steps] == ["data", "validator"]
        assert steps[1].depends_on == ["data"]

    def test_complex_query_returns_two_steps(self):
        steps = build_data_plan("对比北京和上海的岗位数量差异")
        assert [s.id for s in steps] == ["data", "validator"]

    def test_default_no_query_returns_two_steps(self):
        steps = build_data_plan()
        assert [s.id for s in steps] == ["data", "validator"]
