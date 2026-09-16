"""查询复杂度评估：决定走哪条执行链路。"""
from __future__ import annotations

# 复杂度级别
SIMPLE = "simple"      # 单聚合/单筛选，跳过 validator
MEDIUM = "medium"      # 多聚合词或含筛选条件，走完整链路（暂同 NORMAL）
NORMAL = "normal"      # 默认链路
COMPLEX = "complex"    # 多表/嵌套/对比，增强校验

# 简单问题特征：单聚合词 + 单维度
_SIMPLE_PATTERNS = [
    "总计", "总和", "平均", "最多", "最少", "数量", "总数", "总",
]

# 筛选/过滤条件特征
_FILTER_PATTERNS = [
    "筛选", "过滤", "其中", "只看", "只要", "仅", "条件", "限定",
]

# 复杂问题特征
_COMPLEX_PATTERNS = [
    "对比", "差异", "嵌套", "子查询", "多表", "关联", "join",
    "环比", "同比", "占比", "分布", "趋势", "排名变化",
]


def assess_complexity(query: str) -> str:
    """评估查询复杂度。"""
    q = query.lower()

    # 复杂特征命中 → complex
    for pattern in _COMPLEX_PATTERNS:
        if pattern in q:
            return COMPLEX

    # 统计聚合词命中数与筛选条件
    agg_count = sum(1 for p in _SIMPLE_PATTERNS if p in q)
    has_filter = any(p in q for p in _FILTER_PATTERNS)

    # 简单特征：短问题 + 恰好单聚合词 + 无筛选条件
    if len(query) < 20 and agg_count == 1 and not has_filter:
        return SIMPLE

    # 中等特征：多个聚合词，或含筛选/过滤条件
    if agg_count >= 2 or has_filter:
        return MEDIUM

    return NORMAL
