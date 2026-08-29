"""MockLLMClient 规则版 NL2SQL 单测（契约 §10/§14）。generate_json 是 async，用 asyncio.run 调用。"""
import asyncio

from app.core.llm import MockLLMClient

SYSTEM = "任意 system prompt（mock 不使用）"


def _ask(query: str, table: str = "ds_demo") -> dict:
    user = f"用户问题：{query}\n表名：{table}"
    return asyncio.run(MockLLMClient().generate_json(SYSTEM, user))


def test_group_by_region_sum_sales():
    result = _ask("按地区统计总销售额")
    sql = result["sql"]
    assert "GROUP BY region" in sql
    assert "SUM(sales)" in sql
    assert "ORDER BY" in sql
    assert "LIMIT" in sql
    assert result["explanation"]


def test_avg_quantity():
    result = _ask("按商品统计平均销量")
    sql = result["sql"]
    assert "GROUP BY product" in sql
    assert "AVG(quantity)" in sql


def test_top_n_products():
    result = _ask("销量 Top 5 的商品")
    sql = result["sql"]
    assert "GROUP BY product" in sql
    assert "LIMIT 5" in sql
    assert "SUM(quantity)" in sql


def test_overall_total():
    result = _ask("总销售额是多少")
    sql = result["sql"]
    assert "SUM(sales)" in sql
    assert "GROUP BY" not in sql


def test_fallback_select_star():
    """规则全不命中时兜底 SELECT * ... LIMIT 100。"""
    result = _ask("随便看看这份数据")
    assert result["sql"] == "SELECT * FROM ds_demo LIMIT 100"


def test_table_name_from_prompt():
    """表名从 prompt 的「表名：」行提取。"""
    result = _ask("按地区统计总销售额", table="ds_abcd1234")
    assert "FROM ds_abcd1234" in result["sql"]
