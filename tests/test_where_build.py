"""
Tests for sql_generator/where_build.py
Run: python tests/test_where_build.py --url sqlite:///:memory:
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from sql_generator.where_build import (
    escape_identifier, sql_where, build_update, build_select, build_insert,
)
from tests.conftest import parse_url


# ── escape_identifier ─────────────────────────────────────────────────────────

def test_escape_sqlite():
    assert escape_identifier("my_col", "sqlite") == '"my_col"'

def test_escape_oracle():
    assert escape_identifier("MY_COL", "oracle") == '"MY_COL"'

def test_escape_mysql():
    assert escape_identifier("my_col", "mysql") == "`my_col`"

def test_escape_mssql():
    assert escape_identifier("my_col", "mssql") == "[my_col]"

def test_escape_double_quote_in_name():
    result = escape_identifier('col"name', "sqlite")
    assert '""' in result


# ── sql_where ─────────────────────────────────────────────────────────────────

def test_sql_where_tuple():
    sql, params = sql_where([("age", "=", 30)], dialect="sqlite")
    assert ":age_1" in sql
    assert params["age_1"] == 30

def test_sql_where_string():
    sql, params = sql_where(["status = 'active'"], dialect="sqlite")
    assert "status" in sql

def test_sql_where_in():
    sql, params = sql_where([("dept", "IN", "('A','B')")], dialect="sqlite")
    assert "IN" in sql
    assert len([k for k in params if k.startswith("dept")]) == 2

def test_sql_where_between():
    sql, params = sql_where(["salary BETWEEN '50000' and '90000'"], dialect="sqlite")
    assert "BETWEEN" in sql

def test_sql_where_like():
    sql, params = sql_where([("name", "LIKE", "Ali")], dialect="sqlite")
    assert "LIKE" in sql
    assert "%" in list(params.values())[0]

def test_sql_where_expression():
    sql, params = sql_where(
        [("a", "=", 1), ("b", "=", 2)],
        expression="1 AND 2",
        dialect="sqlite"
    )
    assert "AND" in sql

def test_sql_where_or_expression():
    sql, params = sql_where(
        [("a", "=", 1), ("b", "=", 2)],
        expression="1 OR 2",
        dialect="sqlite"
    )
    assert "OR" in sql

def test_sql_where_invalid_dialect():
    try:
        sql_where([("a", "=", 1)], dialect="db2")
        assert False, "should raise"
    except ValueError:
        pass


# ── build_update ──────────────────────────────────────────────────────────────

def test_build_update_basic():
    df = pd.DataFrame([{"name": "Alice", "salary": 95000}])
    sql, params = build_update(df, "employees", [("emp_id", "=", 1)], dialect="sqlite")
    assert "UPDATE" in sql
    assert "SET" in sql
    assert "WHERE" in sql

def test_build_update_case_insensitive_where():
    df = pd.DataFrame([{"salary": 95000}])
    sql, params = build_update(
        df, "employees", [("EMP_ID", "=", 1)],
        dialect="sqlite", allow_missing_where_cols=True
    )
    assert "WHERE" in sql

def test_build_update_multi_row_raises():
    df = pd.DataFrame([{"a": 1}, {"a": 2}])
    try:
        build_update(df, "t", [("id", "=", 1)], dialect="sqlite")
        assert False, "should raise"
    except ValueError:
        pass

def test_build_update_specific_cols():
    df = pd.DataFrame([{"name": "Alice", "salary": 95000, "dept": "Eng"}])
    sql, params = build_update(
        df, "employees", [("emp_id", "=", 1)],
        dialect="sqlite", update_cols=["salary"], allow_missing_where_cols=True
    )
    assert "salary" in sql
    assert "name" not in sql


# ── build_select ──────────────────────────────────────────────────────────────

def test_build_select_basic():
    df = pd.DataFrame([{"emp_id": 1}])
    sql, params = build_select(df, "employees", [("emp_id", "=", "?")], dialect="sqlite")
    assert "SELECT" in sql
    assert "WHERE" in sql

def test_build_select_specific_columns():
    df = pd.DataFrame([{"emp_id": 1}])
    sql, params = build_select(
        df, "employees", [("emp_id", "=", "?")],
        dialect="sqlite", columns=["name", "salary"]
    )
    assert "name" in sql
    assert "salary" in sql
    assert "SELECT *" not in sql


# ── build_insert ──────────────────────────────────────────────────────────────

def test_build_insert_basic():
    df = pd.DataFrame([{"emp_id": 10, "name": "Dave", "dept": "IT", "salary": 60000}])
    sql, params = build_insert(df, "employees", dialect="sqlite")
    assert "INSERT INTO" in sql
    assert "VALUES" in sql
    assert len(params) == 4

def test_build_insert_multi_row_raises():
    df = pd.DataFrame([{"a": 1}, {"a": 2}])
    try:
        build_insert(df, "t", dialect="sqlite")
        assert False, "should raise"
    except ValueError:
        pass


# ── runner ────────────────────────────────────────────────────────────────────

def run_all():
    tests = [
        test_escape_sqlite,
        test_escape_oracle,
        test_escape_mysql,
        test_escape_mssql,
        test_escape_double_quote_in_name,
        test_sql_where_tuple,
        test_sql_where_string,
        test_sql_where_in,
        test_sql_where_between,
        test_sql_where_like,
        test_sql_where_expression,
        test_sql_where_or_expression,
        test_sql_where_invalid_dialect,
        test_build_update_basic,
        test_build_update_case_insensitive_where,
        test_build_update_multi_row_raises,
        test_build_update_specific_cols,
        test_build_select_basic,
        test_build_select_specific_columns,
        test_build_insert_basic,
        test_build_insert_multi_row_raises,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    return passed, failed


if __name__ == "__main__":
    url = parse_url()
    print(f"\n=== test_where_build  [{url}] ===")
    p, f = run_all()
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
