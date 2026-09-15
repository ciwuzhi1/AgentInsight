"""Few-shot 检索：从历史成功 SQL 中找相似问题作为 LLM 示例。"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

# 进程内历史库：question -> sql 映射，LRU 上限 500
_history: list[dict] = []  # [{"question": str, "sql": str, "explanation": str}]
_MAX_HISTORY = 500

# 持久化文件位置（相对 backend 根目录）
_HISTORY_FILE = Path(__file__).resolve().parents[2] / "data" / "fewshot_history.json"


def _tokenize(text: str) -> list[str]:
    """分词：英文单词 + 中文单字 + 中文双字词(bigram)。

    bigram 能显著提升中文短语（如「销售额」「按地区」）的相似度匹配。
    """
    tokens = re.findall(r'[a-zA-Z]+|[一-鿿]', text.lower())
    # 在连续中文字符序列上生成 bigram（join 为字符串保证可哈希）
    bigrams: list[str] = []
    run: list[str] = []
    for tok in tokens:
        if '一' <= tok <= '鿿':
            run.append(tok)
        else:
            if len(run) >= 2:
                bigrams.extend(''.join(run[i:i+2]) for i in range(len(run) - 1))
            run = []
    if len(run) >= 2:
        bigrams.extend(''.join(run[i:i+2]) for i in range(len(run) - 1))
    return tokens + bigrams


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


def export_history(path: Path | str | None = None) -> Path:
    """将历史库导出为 JSON 文件。返回写入路径。"""
    target = Path(path) if path else _HISTORY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def import_history(path: Path | str | None = None) -> int:
    """从 JSON 文件加载历史库，返回加载条数。文件不存在或损坏时返回 0。"""
    global _history
    source = Path(path) if path else _HISTORY_FILE
    if not source.exists():
        return 0
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(data, list):
        return 0
    # 只保留合法条目
    loaded = [
        item for item in data
        if isinstance(item, dict)
        and isinstance(item.get("question"), str)
        and isinstance(item.get("sql"), str)
    ]
    _history = loaded[-_MAX_HISTORY:]
    return len(_history)


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
