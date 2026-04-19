"""
Tests for pipeline.py  (end-to-end: aligner + sql_generator)
Run: python tests/test_pipeline.py --url sqlite:///:memory:
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import sqlalchemy as sa

from pipeline import run
from aligner import AlignmentPolicies
from aligner.policies import ExtraDfColumnsAction
from tests.conftest import make_engine, create_employees, drop_employees, seed_employees, fetch_all, parse_url


# ── insert mode ───────────────────────────────────────────────────────────────

def test_pipeline_insert(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 10, "name": "Dave", "dept": "IT", "salary": 60000.0}])
    out = run(engine, df, "employees", schema=schema_name, mode="insert")
    assert out["result"] == 1
    rows = fetch_all(engine, "employees")
    assert any(r["emp_id"] == 10 for r in rows)

def test_pipeline_insert_drops_extra_col(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 11, "name": "Eve", "dept": "HR", "salary": 55000.0, "ghost": "drop"}])
    out = run(engine, df, "employees", schema=schema_name, mode="insert")
    assert "ghost" in out["coercion_report"].dropped_columns
    assert out["result"] == 1

def test_pipeline_insert_empty(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    out = run(engine, pd.DataFrame(), "employees", schema=schema_name, mode="insert")
    assert out["result"] == 0

def test_pipeline_insert_alignment_error_raises(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    # null in NOT NULL column — analyze will raise
    df = pd.DataFrame([{"emp_id": None, "name": "X", "dept": "Y", "salary": 1.0}])
    try:
        run(engine, df, "employees", schema=schema_name, mode="insert")
        assert False, "should raise"
    except RuntimeError as e:
        assert "Alignment errors" in str(e)


# ── upsert mode ───────────────────────────────────────────────────────────────

def test_pipeline_upsert_new_row(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 50, "name": "Hank", "dept": "Ops", "salary": 72000.0}])
    out = run(engine, df, "employees", schema=schema_name, mode="upsert", constrain=["emp_id"])
    assert out["result"]["success"] == 1
    rows = fetch_all(engine, "employees")
    assert any(r["emp_id"] == 50 for r in rows)

def test_pipeline_upsert_existing_row(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "name": "Alice", "dept": "Engineering", "salary": 99000.0}])
    out = run(engine, df, "employees", schema=schema_name, mode="upsert", constrain=["emp_id"])
    assert out["result"]["success"] == 1
    rows = fetch_all(engine, "employees")
    alice = next(r for r in rows if r["emp_id"] == 1)
    assert alice["salary"] == 99000.0

def test_pipeline_upsert_missing_constrain_raises(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    df = pd.DataFrame([{"emp_id": 1, "name": "X", "dept": "Y", "salary": 1.0}])
    try:
        run(engine, df, "employees", schema=schema_name, mode="upsert")
        assert False, "should raise"
    except ValueError as e:
        assert "constrain" in str(e)


# ── update mode ───────────────────────────────────────────────────────────────

def test_pipeline_update(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "salary": 99999.0}])
    out = run(engine, df, "employees", schema=schema_name, mode="update", where=[("emp_id", "=", "?")])
    assert out["result"] == 1
    rows = fetch_all(engine, "employees")
    alice = next(r for r in rows if r["emp_id"] == 1)
    assert alice["salary"] == 99999.0

def test_pipeline_update_no_match(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 9999, "salary": 1.0}])
    out = run(engine, df, "employees", schema=schema_name, mode="update", where=[("emp_id", "=", "?")])
    assert out["result"] == 0

def test_pipeline_update_missing_where_raises(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    df = pd.DataFrame([{"salary": 1.0}])
    try:
        run(engine, df, "employees", schema=schema_name, mode="update")
        assert False, "should raise"
    except ValueError as e:
        assert "where" in str(e)


# ── update_track mode ─────────────────────────────────────────────────────────

def test_pipeline_update_track(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "salary": 99999.0}])
    out = run(engine, df, "employees", schema=schema_name, mode="update_track", where=[("emp_id", "=", "?")])
    assert out["result"]["tracked"] == 1
    assert out["result"]["updated"] == 1
    insp = sa.inspect(engine)
    assert "employees_tracker" in insp.get_table_names()

def test_pipeline_update_track_snapshot_correct(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 2, "salary": 80000.0}])
    run(engine, df, "employees", schema=schema_name, mode="update_track", where=[("emp_id", "=", "?")])
    tracker = fetch_all(engine, "employees_tracker")
    snap = next(r for r in tracker if r["emp_id"] == 2)
    assert snap["salary"] == 70000  # original value


# ── policies ──────────────────────────────────────────────────────────────────

def test_pipeline_custom_policies(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    seed_employees(engine)
    policies = AlignmentPolicies()
    policies.columns.extra_df_columns_action = ExtraDfColumnsAction.DROP
    df = pd.DataFrame([{"emp_id": 12, "name": "Ian", "dept": "IT", "salary": 65000.0, "extra": "x"}])
    out = run(engine, df, "employees", schema=schema_name, mode="insert", policies=policies)
    assert out["result"] == 1
    assert "extra" in out["coercion_report"].dropped_columns

def test_pipeline_invalid_mode(engine):
    schema_name = "main" if engine.dialect.name == "sqlite" else None
    df = pd.DataFrame([{"emp_id": 1}])
    try:
        run(engine, df, "employees", schema=schema_name, mode="delete")
        assert False, "should raise"
    except ValueError as e:
        assert "mode" in str(e)


# ── runner ────────────────────────────────────────────────────────────────────

def run_all(url: str):
    engine = make_engine(url)

    tests = [
        ("test_pipeline_insert",                   test_pipeline_insert),
        ("test_pipeline_insert_drops_extra_col",   test_pipeline_insert_drops_extra_col),
        ("test_pipeline_insert_empty",             test_pipeline_insert_empty),
        ("test_pipeline_insert_alignment_error",   test_pipeline_insert_alignment_error_raises),
        ("test_pipeline_upsert_new_row",           test_pipeline_upsert_new_row),
        ("test_pipeline_upsert_existing_row",      test_pipeline_upsert_existing_row),
        ("test_pipeline_upsert_missing_constrain", test_pipeline_upsert_missing_constrain_raises),
        ("test_pipeline_update",                   test_pipeline_update),
        ("test_pipeline_update_no_match",          test_pipeline_update_no_match),
        ("test_pipeline_update_missing_where",     test_pipeline_update_missing_where_raises),
        ("test_pipeline_update_track",             test_pipeline_update_track),
        ("test_pipeline_update_track_snapshot",    test_pipeline_update_track_snapshot_correct),
        ("test_pipeline_custom_policies",          test_pipeline_custom_policies),
        ("test_pipeline_invalid_mode",             test_pipeline_invalid_mode),
    ]

    passed = failed = 0
    for name, t in tests:
        drop_employees(engine)
        create_employees(engine)
        try:
            t(engine)
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1

    drop_employees(engine)
    return passed, failed


if __name__ == "__main__":
    url = parse_url()
    print(f"\n=== test_pipeline  [{url}] ===")
    p, f = run_all(url)
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
