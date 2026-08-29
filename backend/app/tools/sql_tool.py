"""SQL Guard：只读校验与 LIMIT 规范化（契约 §5）。"""
from __future__ import annotations

import re


class SQLGuardError(Exception):
    """SQL 未通过 guard 校验。"""


# 行注释与块注释剥离
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# 允许的前导字符：空白与括号
_START_RE = re.compile(r"^[\s(]*(SELECT|WITH)\b", re.I)

# 写操作黑名单：独立 token、大小写不敏感
_BLACKLIST = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH",
    "DETACH", "COPY", "EXPORT", "INSTALL", "LOAD", "CALL", "SET",
    "PRAGMA", "GRANT", "REVOKE", "TRUNCATE", "VACUUM", "CHECKPOINT",
)
_BLACKLIST_RES = [
    re.compile(rf"\b{word}\b", re.I) for word in _BLACKLIST
]

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.I)


def guard(sql: str, max_rows: int) -> tuple[bool, str, str | None]:
    """校验并规范化 SQL。

    返回 (ok, sql_clean, reason)：
    1. 仅允许单条语句（去注释后不得含第二个分号）；
    2. 必须以 SELECT/WITH 开头（允许前置空白与括号）；
    3. 写操作黑名单 token 直接拒绝；
    4. 无 LIMIT 补 LIMIT max_rows；LIMIT 超上限改写为 max_rows。
    """
    if not sql or not sql.strip():
        return False, "", "SQL 为空"

    # 剥离注释
    clean = _BLOCK_COMMENT.sub(" ", sql)
    clean = _LINE_COMMENT.sub(" ", clean)
    clean = clean.strip()

    # 规则 1：单条语句——剥掉末尾分号后不得再有分号
    body = clean
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        return False, "", "仅允许单条 SELECT 语句"

    # 规则 2：必须以 SELECT/WITH 开头
    if not _START_RE.match(body):
        return False, "", "仅允许 SELECT/WITH 查询"

    # 规则 3：黑名单 token
    for pattern in _BLACKLIST_RES:
        m = pattern.search(body)
        if m:
            return False, "", f"禁止使用关键词: {m.group(0).upper()}"

    # 规则 4：LIMIT 规范化
    limits = _LIMIT_RE.findall(body)
    if not limits:
        body = f"{body} LIMIT {max_rows}"
    elif any(int(v) > max_rows for v in limits):

        def _cap(m: re.Match) -> str:
            # 超上限的 LIMIT 值改写为 max_rows，其余保持原样
            return f"LIMIT {max_rows}" if int(m.group(1)) > max_rows else m.group(0)

        body = _LIMIT_RE.sub(_cap, body)
    return True, body, None
