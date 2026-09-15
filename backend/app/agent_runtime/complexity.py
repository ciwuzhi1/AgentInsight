"""查询复杂度评估：决定走哪条执行链路。"""
from __future__ import annotations

# 复杂度级别
SIMPLE = "simple"      # 单聚合/单筛选，跳过 validator
NORMAL = "normal"      # 默认链路
COMPLEX = "complex"    # 多表/嵌套/对比，增强校验

# 简单问题特征：单聚合词 + 单维度
_SIMPLE_PATTERNS = [
    "总计", "总和", "平均", "最多", "最少", "数量", "总数", "总",
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

    # 简单特征：短问题 + 含聚合词 + 无复杂词
    if len(query) < 20:
        for pattern in _SIMPLE_PATTERNS:
            if pattern in q:
                # 确认没有复杂特征
                has_complex = any(p in q for p in _COMPLEX_PATTERNS)
                if not has_complex:
                    return SIMPLE

    return NORMAL
