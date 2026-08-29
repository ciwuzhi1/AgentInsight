"""SQL Guard 单测（契约 §5/§14）。

guard(sql, max_rows) -> (ok: bool, sql_clean: str, reason: str | None)
"""
from app.tools.sql_tool import guard

MAX_ROWS = 1000


def test_simple_select_passes():
    ok, sql, reason = guard(
        "SELECT region, SUM(sales) FROM ds_demo GROUP BY region", MAX_ROWS
    )
    assert ok
    assert reason is None
    # 无 LIMIT 自动补
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_with_cte_passes():
    ok, _, _ = guard(
        "WITH t AS (SELECT region FROM ds_demo) SELECT region FROM t", MAX_ROWS
    )
    assert ok


def test_leading_parenthesis_and_whitespace_passes():
    ok, _, _ = guard("  \n (SELECT 1", MAX_ROWS)
    assert ok


def test_empty_sql_rejected():
    ok, sql, reason = guard("   ", MAX_ROWS)
    assert not ok
    assert sql == ""
    assert reason


def test_blacklist_rejected():
    cases = [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN c INT",
        "CREATE TABLE t (a INT)",
        "ATTACH 'x.db' AS x",
        "COPY t TO 'x.csv'",
        "INSTALL httpfs",
        "CALL something()",
        "GRANT ALL ON t TO u",
        "TRUNCATE t",
    ]
    for bad in cases:
        ok, sql, reason = guard(bad, MAX_ROWS)
        assert not ok, f"应拒绝: {bad}"
        assert sql == ""
        assert reason


def test_pragma_rejected_case_insensitive():
    """PRAGMA 被拒（或被规则 2 的「必须 SELECT/WITH 开头」先拦下，同样拒绝）。"""
    for bad in ("pragma table_info('t')", "PRAGMA database_list", "Pragma show_tables"):
        ok, _, reason = guard(bad, MAX_ROWS)
        assert not ok, bad
        assert reason


def test_multi_statement_rejected():
    ok, _, reason = guard("SELECT 1; SELECT 2", MAX_ROWS)
    assert not ok
    assert "单条" in reason


def test_second_statement_hidden_after_comment_rejected():
    """真实第二条语句即使后面跟注释也必须拒绝。"""
    ok, _, reason = guard("SELECT 1; DROP TABLE t -- just a comment", MAX_ROWS)
    assert not ok


def test_semicolon_and_blacklist_inside_comment_are_stripped():
    """注释里的分号与黑名单词会被剥离，不构成违规。"""
    ok, sql, reason = guard("SELECT 1 /* ; DROP TABLE t */", MAX_ROWS)
    assert ok
    assert reason is None
    assert "DROP" not in sql


def test_line_comment_then_trailing_semicolon_passes():
    ok, sql, _ = guard("SELECT region FROM ds_demo -- 按地区查\n;", MAX_ROWS)
    assert ok
    assert sql.startswith("SELECT region FROM ds_demo")
    assert ";" not in sql


def test_missing_limit_appended():
    ok, sql, _ = guard("SELECT * FROM ds_demo", 500)
    assert ok
    assert sql == "SELECT * FROM ds_demo LIMIT 500"


def test_limit_over_cap_rewritten():
    ok, sql, _ = guard("SELECT * FROM ds_demo LIMIT 99999", MAX_ROWS)
    assert ok
    assert "LIMIT 1000" in sql
    assert "99999" not in sql


def test_limit_within_cap_untouched():
    ok, sql, _ = guard("SELECT * FROM ds_demo LIMIT 10", MAX_ROWS)
    assert ok
    assert "LIMIT 10" in sql
    assert f"LIMIT {MAX_ROWS}" not in sql
