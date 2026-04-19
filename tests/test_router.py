"""
Tests for sql_generator/router.py  (insert, upsert, update)
Run: python tests/test_router.py --url sqlite:///:memory:
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import sqlalchemy as sa

from sql_generator import insert, upsert, update
from tests.conftest import make_engine, create_employees, drop_employees, seed_employees, fetch_all, parse_url


# ── insert ────────────────────────────────────────────────────────────────────

def test_insert_dataframe(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 10, "name": "Dave", "dept": "IT", "salary": 60000}])
    count = insert(engine, df, "employees")
    assert count == 1
    rows = fetch_all(engine, "employees")
    assert any(r["emp_id"] == 10 for r in rows)

def test_insert_dict(engine):
    seed_employees(engine)
    count = insert(engine, {"emp_id": 11, "name": "Eve", "dept": "HR", "salary": 55000}, "employees")
    assert count == 1

def test_insert_list(engine):
    seed_employees(engine)
    data = [
        {"emp_id": 20, "name": "Frank", "dept": "IT", "salary": 62000},
        {"emp_id": 21, "name": "Grace", "dept": "IT", "salary": 63000},
    ]
    count = insert(engine, data, "employees")
    assert count == 2

def test_insert_empty(engine):
    count = insert(engine, [], "employees")
    assert count == 0

def test_insert_invalid_column(engine):
    seed_employees(engine)
    try:
        insert(engine, {"emp_id": 99, "ghost": "bad"}, "employees")
        assert False, "should raise"
    except ValueError as e:
        assert "ghost" in str(e)


# ── upsert ────────────────────────────────────────────────────────────────────

def test_upsert_insert_new(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 50, "name": "Hank", "dept": "Ops", "salary": 72000}])
    stats = upsert(engine, df, "employees", constrain=["emp_id"])
    assert stats["success"] == 1
    rows = fetch_all(engine, "employees")
    assert any(r["emp_id"] == 50 for r in rows)

def test_upsert_update_existing(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "name": "Alice Updated", "dept": "Engineering", "salary": 99000}])
    stats = upsert(engine, df, "employees", constrain=["emp_id"])
    assert stats["success"] == 1
    rows = fetch_all(engine, "employees")
    alice = next(r for r in rows if r["emp_id"] == 1)
    assert alice["salary"] == 99000

def test_upsert_empty(engine):
    stats = upsert(engine, [], "employees", constrain=["emp_id"])
    assert stats["total"] == 0

def test_upsert_invalid_constrain(engine):
    seed_employees(engine)
    try:
        upsert(engine, {"emp_id": 1, "name": "X", "dept": "Y", "salary": 1}, "employees", constrain=["ghost_col"])
        assert False, "should raise"
    except ValueError:
        pass


# ── update ────────────────────────────────────────────────────────────────────

def test_update_basic(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"salary": 99999}])
    count = update(engine, "employees", df, where=[("emp_id", "=", 1)])
    assert count == 1
    rows = fetch_all(engine, "employees")
    alice = next(r for r in rows if r["emp_id"] == 1)
    assert alice["salary"] == 99999

def test_update_no_match(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"salary": 1}])
    count = update(engine, "employees", df, where=[("emp_id", "=", 9999)])
    assert count == 0

def test_update_empty_data(engine):
    count = update(engine, "employees", [], where=[("emp_id", "=", 1)])
    assert count == 0

def test_update_multiple_rows(engine):
    seed_employees(engine)
    data = [
        {"emp_id": 1, "salary": 11111},
        {"emp_id": 2, "salary": 22222},
    ]
    count = update(engine, "employees", data, where=[("emp_id", "=", "?")])
    assert count == 2


# ── runner ────────────────────────────────────────────────────────────────────

def run_all(url: str):
    engine = make_engine(url)
    create_employees(engine)

    tests = [
        lambda: test_insert_dataframe(engine),
        lambda: test_insert_dict(engine),
        lambda: test_insert_list(engine),
        lambda: test_insert_empty(engine),
        lambda: test_insert_invalid_column(engine),
        lambda: test_upsert_insert_new(engine),
        lambda: test_upsert_update_existing(engine),
        lambda: test_upsert_empty(engine),
        lambda: test_upsert_invalid_constrain(engine),
        lambda: test_update_basic(engine),
        lambda: test_update_no_match(engine),
        lambda: test_update_empty_data(engine),
        lambda: test_update_multiple_rows(engine),
    ]

    names = [
        "test_insert_dataframe", "test_insert_dict", "test_insert_list",
        "test_insert_empty", "test_insert_invalid_column",
        "test_upsert_insert_new", "test_upsert_update_existing",
        "test_upsert_empty", "test_upsert_invalid_constrain",
        "test_update_basic", "test_update_no_match",
        "test_update_empty_data", "test_update_multiple_rows",
    ]

    passed = failed = 0
    for name, t in zip(names, tests):
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
    url = parse_url()
    print(f"\n=== test_router  [{url}] ===")
    p, f = run_all(url)
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
