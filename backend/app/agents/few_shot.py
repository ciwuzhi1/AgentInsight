"""Few-shot 检索：从历史成功 SQL 中找相似问题作为 LLM 示例。"""
from __future__ import annotations

import math
from collections import Counter

# 进程内历史库：question -> sql 映射，LRU 上限 500
_history: list[dict] = []  # [{"question": str, "sql": str, "explanation": str}]
_MAX_HISTORY = 500


def _tokenize(text: str) -> list[str]:
    """简单分词：按空格/标点切分 + 中文字符逐字。"""
    import re
    # 英文单词 + 中文单字
    tokens = re.findall(r'[a-zA-Z]+|[\u4e00-\u9fff]', text.lower())
    return tokens


def _tfidf_vector(text: str, vocab: dict[str, float]) -> dict[str, float]:
    """将文本转为 TF-IDF 向量。"""
    tokens = _tokenize(text)
    if not tokens:
        return {}
    tf = Counter(tokens)
    total = len(tokens)
    vec = {}
    for token, count in tf.items():
        if token in vocab:
            vec[token] = (count / total) * vocab[token]
    return vec


def _cosine_sim(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """余弦相似度。"""
    common = set(vec1.keys()) & set(vec2.keys())
    if not common:
        return 0.0
    dot = sum(vec1[k] * vec2[k] for k in common)
    n1 = math.sqrt(sum(v*v for v in vec1.values()))
    n2 = math.sqrt(sum(v*v for v in vec2.values()))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def _build_vocab() -> dict[str, float]:
    """从历史库构建 IDF 词表。"""
    if not _history:
        return {}
    df = Counter()
    for item in _history:
        tokens = set(_tokenize(item["question"]))
        for t in tokens:
            df[t] += 1
    n = len(_history)
    return {t: math.log(n / (f + 1)) + 1 for t, f in df.items()}


def retrieve_similar(query: str, top_k: int = 3) -> list[dict]:
    """检索与 query 最相似的历史问题。"""
    if not _history:
        return []
    vocab = _build_vocab()
    query_vec = _tfidf_vector(query, vocab)

    scored = []
    for item in _history:
        item_vec = _tfidf_vector(item["question"], vocab)
        sim = _cosine_sim(query_vec, item_vec)
        if sim > 0.1:  # 相似度阈值
            scored.append((sim, item))

    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:top_k]]


def add_to_history(question: str, sql: str, explanation: str) -> None:
    """SQL 执行成功后存入历史库。"""
    global _history
    # 去重
    for item in _history:
        if item["question"] == question:
            item["sql"] = sql
            item["explanation"] = explanation
            return
    _history.append({"question": question, "sql": sql, "explanation": explanation})
    # LRU 淘汰
    if len(_history) > _MAX_HISTORY:
        _history = _history[-_MAX_HISTORY:]


def format_few_shot_prompt(query: str, schema_str: str, table: str, top_k: int = 3) -> str:
    """构造包含 few-shot 示例的 user prompt。"""
    examples = retrieve_similar(query, top_k)

    parts = [f"表名: {table}\n字段:\n{schema_str}\n"]

    if examples:
        parts.append("参考示例（相似问题的 SQL）:")
        for i, ex in enumerate(examples, 1):
            parts.append(f"示例{i}:")
            parts.append(f"  问题: {ex['question']}")
            parts.append(f"  SQL: {ex['sql']}")
        parts.append("")

    parts.append(f"用户问题: {query}")
    return "\n".join(parts)
