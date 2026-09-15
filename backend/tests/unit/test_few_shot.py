"""Few-shot 检索单测。历史库是进程内全局状态，每个用例先重置。"""
from app.agents import few_shot
from app.agents.few_shot import (
    add_to_history,
    format_few_shot_prompt,
    retrieve_similar,
)


def setup_function():
    """每个测试前清空历史库，避免全局状态泄漏。"""
    few_shot._history = []


def test_retrieve_empty_history():
    assert retrieve_similar("按地区统计总销售额") == []


def test_add_and_retrieve_similar():
    add_to_history(
        "按地区统计总销售额",
        "SELECT region, SUM(sales) FROM ds_demo GROUP BY region",
        "按地区汇总",
    )
    add_to_history(
        "看看各商品的平均销量",
        "SELECT product, AVG(quantity) FROM ds_demo GROUP BY product",
        "按商品求均值",
    )
    results = retrieve_similar("按地区统计销售额是多少")
    assert results, "应检索到相似历史问题"
    assert results[0]["question"] == "按地区统计总销售额"
    assert "GROUP BY region" in results[0]["sql"]


def test_retrieve_unrelated_returns_empty():
    add_to_history(
        "按地区统计总销售额",
        "SELECT region, SUM(sales) FROM ds_demo GROUP BY region",
        "按地区汇总",
    )
    # 完全不相关的英文问题，分词后无交集 → 相似度 0
    assert retrieve_similar("totally unrelated english query xyz") == []


def test_add_to_history_dedup_updates():
    add_to_history("q1", "SELECT 1", "a")
    add_to_history("q1", "SELECT 2", "b")
    assert len(few_shot._history) == 1
    assert few_shot._history[0]["sql"] == "SELECT 2"
    assert few_shot._history[0]["explanation"] == "b"


def test_lru_eviction_at_500():
    for i in range(few_shot._MAX_HISTORY + 10):
        add_to_history(f"question number {i}", f"SELECT {i}", f"exp {i}")
    assert len(few_shot._history) == few_shot._MAX_HISTORY
    # 最老的应被淘汰
    assert few_shot._history[0]["question"] == "question number 10"
    assert few_shot._history[-1]["question"] == "question number 509"


def test_format_prompt_includes_examples():
    add_to_history(
        "按地区统计总销售额",
        "SELECT region, SUM(sales) FROM ds_demo GROUP BY region",
        "按地区汇总",
    )
    prompt = format_few_shot_prompt(
        "按地区统计总销售额是多少",
        "region TEXT\nsales DOUBLE",
        "ds_demo",
    )
    assert "表名: ds_demo" in prompt
    assert "region TEXT" in prompt
    assert "参考示例" in prompt
    assert "示例1:" in prompt
    assert "SELECT region, SUM(sales)" in prompt
    assert "用户问题: 按地区统计总销售额是多少" in prompt


def test_format_prompt_no_examples():
    """历史为空时不出现示例段。"""
    prompt = format_few_shot_prompt("任意问题", "col INT", "t1")
    assert "参考示例" not in prompt
    assert "用户问题: 任意问题" in prompt
    assert "表名: t1" in prompt
