"""Token 预算控制：限制 LLM 调用的 token 消耗。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenBudget:
    """LLM 调用的 token 上限；缺省值覆盖常规匹配 / NL2SQL 场景。"""

    max_input_tokens: int = 4000
    max_output_tokens: int = 1000
    max_context_tokens: int = 8000

    def check_input(self, text: str) -> bool:
        """粗略估算 token 数（中文 1 字 ≈ 1 token，英文 1 词 ≈ 1.3 token）。"""
        return estimate_tokens(text) <= self.max_input_tokens

    def truncate_input(self, text: str) -> str:
        """截断到预算内。"""
        if self.check_input(text):
            return text
        # 按字符粗略截断
        ratio = self.max_input_tokens / max(estimate_tokens(text), 1)
        return text[: int(len(text) * ratio)]


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。"""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return chinese_chars + int(other_chars / 4)
