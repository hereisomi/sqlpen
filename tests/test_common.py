"""
Tests for sql_generator/common.py
Run: python tests/test_common.py --url sqlite:///:memory:
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import sqlalchemy as sa

from sql_generator.common import (
    normalize_data, chunk_rows, validate_columns_exist,
    exec_with_row_isolation, ensure_connection, get_table,
)
from tests.conftest import make_engine, create_employees, drop_employees


# ── normalize_data ────────────────────────────────────────────────────────────

def test_normalize_dict():
    rows = normalize_data({"a": 1, "b": 2})
    assert rows == [{"a": 1, "b": 2}]

def test_normalize_list():
    rows = normalize_data([{"a": 1}, {"a": 2}])
    assert rows == [{"a": 1}, {"a": 2}]

def test_normalize_dataframe():
    df = pd.DataFrame([{"a": 1, "b": None}])
    rows = normalize_data(df)
    assert rows == [{"a": 1, "b": None}]

def test_normalize_dataframe_nan_becomes_none():
    import numpy as np
    df = pd.DataFrame([{"a": np.nan}])
    rows = normalize_data(df)
    assert rows[0]["a"] is None

def test_normalize_empty_list():
    assert normalize_data([]) == []

def test_normalize_invalid_type():
    try:
        normalize_data(42)
        assert False, "should raise"
    except TypeError:
        pass


# ── chunk_rows ────────────────────────────────────────────────────────────────

def test_chunk_rows_even():
    rows = [{"i": i} for i in range(6)]
    chunks = list(chunk_rows(rows, 2))
    assert len(chunks) == 3
    assert chunks[0] == [{"i": 0}, {"i": 1}]

def test_chunk_rows_uneven():
    rows = [{"i": i} for i in range(5)]
    chunks = list(chunk_rows(rows, 2))
    assert len(chunks) == 3
    assert chunks[-1] == [{"i": 4}]

def test_chunk_rows_larger_than_list():
    rows = [{"i": 1}]
    chunks = list(chunk_rows(rows, 100))
    assert chunks == [[{"i": 1}]]

def test_chunk_rows_empty():
    assert list(chunk_rows([], 10)) == []


# ── validate_columns_exist ────────────────────────────────────────────────────

def test_validate_columns_exist_ok(engine):
    meta = sa.MetaData()
    tbl = sa.Table("employees", meta, autoload_with=engine)
    rows = [{"emp_id": 1, "name": "X", "dept": "Y", "salary": 1.0}]
    validate_columns_exist(rows, tbl)  # no exception

def test_validate_columns_exist_invalid(engine):
    meta = sa.MetaData()
    tbl = sa.Table("employees", meta, autoload_with=engine)
    rows = [{"emp_id": 1, "ghost_col": "bad"}]
    try:
        validate_columns_exist(rows, tbl)
        assert False, "should raise"
    except ValueError as e:
        assert "ghost_col" in str(e)


# ── exec_with_row_isolation ───────────────────────────────────────────────────

def test_exec_bulk_success():
    called = []
    def bulk(rows): called.append(("bulk", len(rows)))
    def row(r): called.append(("row",))
    stats = exec_with_row_isolation([{"a": 1}, {"a": 2}], bulk, row, tolerance=3)
    assert stats["method"] == "bulk"
    assert stats["success"] == 2
    assert stats["failed"] == 0

def test_exec_fallback_on_bulk_failure():
    def bulk(rows): raise RuntimeError("bulk fail")
    called = []
    def row(r): called.append(r)
    stats = exec_with_row_isolation([{"a": 1}, {"a": 2}], bulk, row, tolerance=5)
    assert stats["method"] == "lazy_fallback"
    assert stats["success"] == 2
    assert len(called) == 2

def test_exec_fallback_partial_failure():
    def bulk(rows): raise RuntimeError("bulk fail")
    def row(r):
        if r["a"] == 2:
            raise ValueError("bad row")
    stats = exec_with_row_isolation([{"a": 1}, {"a": 2}, {"a": 3}], bulk, row, tolerance=5)
    assert stats["success"] == 2
    assert stats["failed"] == 1

def test_exec_all_fail_raises():
    def bulk(rows): raise RuntimeError("bulk fail")
    def row(r): raise ValueError("row fail")
    try:
        exec_with_row_isolation([{"a": 1}], bulk, row, tolerance=5)
        assert False, "should raise"
    except RuntimeError:
        pass

def test_exec_empty_rows():
    stats = exec_with_row_isolation([], lambda r: None, lambda r: None, tolerance=5)
    assert stats["method"] == "none"
    assert stats["total"] == 0

def test_exec_tolerance_abort():
    def bulk(rows): raise RuntimeError("bulk fail")
    def row(r): raise ValueError("always fail")
    try:
        exec_with_row_isolation(
            [{"a": i} for i in range(10)], bulk, row, tolerance=2
        )
        assert False, "should raise"
    except RuntimeError as e:
        assert "Bulk operation failed" in str(e)


# ── runner ────────────────────────────────────────────────────────────────────

def run_all(url: str):
    engine = make_engine(url)
    create_employees(engine)

    # inject engine into tests that need it
    global tmp_engine
    tmp_engine = engine

    tests = [
        test_normalize_dict,
        test_normalize_list,
        test_normalize_dataframe,
        test_normalize_dataframe_nan_becomes_none,
        test_normalize_empty_list,
        test_normalize_invalid_type,
        test_chunk_rows_even,
        test_chunk_rows_uneven,
        test_chunk_rows_larger_than_list,
        test_chunk_rows_empty,
        lambda: test_validate_columns_exist_ok(engine),
        lambda: test_validate_columns_exist_invalid(engine),
        test_exec_bulk_success,
        test_exec_fallback_on_bulk_failure,
        test_exec_fallback_partial_failure,
        test_exec_all_fail_raises,
        test_exec_empty_rows,
        test_exec_tolerance_abort,
    ]

    passed = failed = 0
    for t in tests:
        name = getattr(t, "__name__", repr(t))
        try:
            t()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1

    drop_employees(engine)
    return passed, failed


if __name__ == "__main__":
    from tests.conftest import parse_url
    url = parse_url()
    print(f"\n=== test_common  [{url}] ===")
    p, f = run_all(url)
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
