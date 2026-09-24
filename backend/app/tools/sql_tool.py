"""SQL Guard：只读校验与 LIMIT 规范化（契约 §5）。"""
from __future__ import annotations

import re


class SQLGuardError(Exception):
    """SQL 未通过 guard 校验。"""


# 行注释与块注释剥离（在字符串感知之后调用，避免改写字面量）
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# 允许的前导字符：空白与括号
_START_RE = re.compile(r"^[\s(]*(SELECT|WITH)\b", re.I)

# 写操作黑名单：独立 token、大小写不敏感
_BLACKLIST = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH",
    "DETACH", "COPY", "EXPORT", "INSTALL", "LOAD", "CALL", "SET",
    "GRANT", "REVOKE", "TRUNCATE", "VACUUM", "CHECKPOINT",
)
_BLACKLIST_RES = [
    re.compile(rf"\b{word}\b", re.I) for word in _BLACKLIST
]
# pragma / pragma_table_info 等一并拒绝（\bPRAGMA\b 拦不住下划线续接）
_PRAGMA_RE = re.compile(r"\bpragma(_|\b)", re.I)
# DuckDB 外读/外连表函数：任意文件读 + 二次 SSRF
_TABLE_FUNC_RE = re.compile(
    r"\b(read_csv|read_csv_auto|read_parquet|read_json|read_text|read_blob|"
    r"glob|sniff_csv|parquet_scan|csv_scan|delta_scan|iceberg_scan|"
    r"sqlite_scan|postgres_scan|mysql_scan|read_xlsx|st_read|"
    r"openrowset|opendatasource|url|httpfs)\b",
    re.I,
)
# FROM/JOIN 源必须是已注册视图 ds_*（允许别名与逗号多表）
_FROM_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.I)


def _strip_comments(sql: str) -> str:
    """剥离注释；跳过单引号字符串字面量，避免把字符串内 --/*/ 删掉。"""
    out: list[str] = []
    i, n = 0, len(sql)
    in_str = False
    while i < n:
        ch = sql[i]
        if in_str:
            out.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    out.append(sql[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def guard(sql: str, max_rows: int) -> tuple[bool, str, str | None]:
    """校验并规范化 SQL。

    返回 (ok, sql_clean, reason)：
    1. 仅允许单条语句（去注释后不得含第二个分号）；
    2. 必须以 SELECT/WITH 开头（允许前置空白与括号）；
    3. 写操作黑名单 token / pragma / 外读表函数直接拒绝；
    4. FROM/JOIN 表源必须是已注册视图 ds_*；
    5. 无 LIMIT 补 LIMIT max_rows；LIMIT 超上限改写为 max_rows。
    """
    if not sql or not sql.strip():
        return False, "", "SQL 为空"

    # 剥离注释（字符串感知，避免改写 'a/*b*/c' 之类的字面量）
    clean = _strip_comments(sql).strip()

    # 规则 1：单条语句——剥掉末尾分号后不得再有分号
    body = clean
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        return False, "", "仅允许单条 SELECT 语句"

    # 规则 2：必须以 SELECT/WITH 开头
    if not _START_RE.match(body):
        return False, "", "仅允许 SELECT/WITH 查询"

    # 规则 3：黑名单 token + pragma* + 外读表函数
    if _PRAGMA_RE.search(body):
        return False, "", "禁止使用关键词: PRAGMA"
    for pattern in _BLACKLIST_RES:
        m = pattern.search(body)
        if m:
            return False, "", f"禁止使用关键词: {m.group(0).upper()}"
    m = _TABLE_FUNC_RE.search(body)
    if m:
        return False, "", f"禁止使用表函数: {m.group(0).upper()}"

    # 规则 4：FROM/JOIN 源白名单——已注册视图 ds_* 或本语句 CTE 别名
    cte_names = {
        n.lower()
        for n in re.findall(
            r"(?:WITH|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*AS\s*\(",
            body,
            re.I,
        )
    }
    for m in _FROM_RE.finditer(body):
        src = m.group(1)
        if not src.startswith("ds_") and src.lower() not in cte_names:
            return False, "", f"非法表源: {src}（仅允许 ds_* 视图或 CTE 别名）"

    # 规则 5：LIMIT 规范化
    limits = _LIMIT_RE.findall(body)
    if not limits:
        body = f"{body} LIMIT {max_rows}"
    elif any(int(v) > max_rows for v in limits):

        def _cap(m: re.Match) -> str:
            # 超上限的 LIMIT 值改写为 max_rows，其余保持原样
            return f"LIMIT {max_rows}" if int(m.group(1)) > max_rows else m.group(0)

        body = _LIMIT_RE.sub(_cap, body)
    return True, body, None
