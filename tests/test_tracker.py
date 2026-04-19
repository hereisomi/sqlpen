"""
Tests for sql_generator/tracker.py  (update_track)
Run: python tests/test_tracker.py --url sqlite:///:memory:
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import sqlalchemy as sa

from sql_generator import update_track
from sql_generator.tracker import _extract_key_cols, _build_snapshot_select
from tests.conftest import make_engine, create_employees, drop_employees, seed_employees, fetch_all, parse_url


# ── _extract_key_cols ─────────────────────────────────────────────────────────

def test_extract_key_cols_tuple():
    cols = _extract_key_cols([("emp_id", "=", "?")], {"emp_id": 1})
    assert cols == ["emp_id"]

def test_extract_key_cols_string():
    cols = _extract_key_cols(["emp_id = ?"], {"emp_id": 1})
    assert cols == ["emp_id"]

def test_extract_key_cols_multiple():
    cols = _extract_key_cols([("a", "=", "?"), ("b", "=", "?")], {"a": 1, "b": 2})
    assert "a" in cols and "b" in cols


# ── _build_snapshot_select ────────────────────────────────────────────────────

def test_build_snapshot_select_single_val():
    rows = [{"emp_id": 1, "salary": 90000}]
    sql, params = _build_snapshot_select("employees", ["emp_id"], rows)
    assert 'SELECT * FROM "employees"' in sql
    assert '"emp_id" = :emp_id' in sql
    assert params["emp_id"] == 1

def test_build_snapshot_select_multi_val():
    rows = [{"emp_id": 1, "salary": 90000}, {"emp_id": 2, "salary": 70000}]
    sql, params = _build_snapshot_select("employees", ["emp_id"], rows)
    assert "IN" in sql
    assert '"emp_id" IN' in sql
    assert len(params) == 2

def test_build_snapshot_select_no_vals_raises():
    rows = [{"salary": 90000}]  # emp_id not in rows
    try:
        _build_snapshot_select("employees", ["emp_id"], rows)
        assert False, "should raise"
    except ValueError:
        pass


# ── update_track (integration) ────────────────────────────────────────────────

def test_update_track_creates_tracker(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "salary": 99999}])
    result = update_track(engine, df, "employees", where=[("emp_id", "=", "?")])
    assert result["tracked"] == 1
    assert result["updated"] == 1
    # tracker table must exist
    insp = sa.inspect(engine)
    assert "employees_tracker" in insp.get_table_names()

def test_update_track_snapshot_has_old_value(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "salary": 99999}])
    update_track(engine, df, "employees", where=[("emp_id", "=", "?")])
    tracker_rows = fetch_all(engine, "employees_tracker")
    assert any(r["emp_id"] == 1 and r["salary"] == 90000 for r in tracker_rows)

def test_update_track_actual_update_applied(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 1, "salary": 99999}])
    update_track(engine, df, "employees", where=[("emp_id", "=", "?")])
    rows = fetch_all(engine, "employees")
    alice = next(r for r in rows if r["emp_id"] == 1)
    assert alice["salary"] == 99999

def test_update_track_appends_on_second_call(engine):
    seed_employees(engine)
    df1 = pd.DataFrame([{"emp_id": 1, "salary": 91000}])
    df2 = pd.DataFrame([{"emp_id": 1, "salary": 92000}])
    update_track(engine, df1, "employees", where=[("emp_id", "=", "?")])
    update_track(engine, df2, "employees", where=[("emp_id", "=", "?")])
    tracker_rows = fetch_all(engine, "employees_tracker")
    emp1_snapshots = [r for r in tracker_rows if r["emp_id"] == 1]
    assert len(emp1_snapshots) == 2

def test_update_track_no_match_returns_zero(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 9999, "salary": 1}])
    result = update_track(engine, df, "employees", where=[("emp_id", "=", "?")])
    assert result["tracked"] == 0
    assert result["updated"] == 0

def test_update_track_empty_data(engine):
    result = update_track(engine, [], "employees", where=[("emp_id", "=", "?")])
    assert result == {"tracked": 0, "updated": 0}

def test_update_track_has_timestamp(engine):
    seed_employees(engine)
    df = pd.DataFrame([{"emp_id": 2, "salary": 75000}])
    update_track(engine, df, "employees", where=[("emp_id", "=", "?")])
    tracker_rows = fetch_all(engine, "employees_tracker")
    row = next(r for r in tracker_rows if r["emp_id"] == 2)
    assert row["track_inserted_at"] is not None


# ── runner ────────────────────────────────────────────────────────────────────

def run_all(url: str):
    engine = make_engine(url)
    create_employees(engine)

    unit_tests = [
        test_extract_key_cols_tuple,
        test_extract_key_cols_string,
        test_extract_key_cols_multiple,
        test_build_snapshot_select_single_val,
        test_build_snapshot_select_multi_val,
        test_build_snapshot_select_no_vals_raises,
    ]

    integration_tests = [
        ("test_update_track_creates_tracker",        lambda: test_update_track_creates_tracker(engine)),
        ("test_update_track_snapshot_has_old_value", lambda: test_update_track_snapshot_has_old_value(engine)),
        ("test_update_track_actual_update_applied",  lambda: test_update_track_actual_update_applied(engine)),
        ("test_update_track_appends_on_second_call", lambda: test_update_track_appends_on_second_call(engine)),
        ("test_update_track_no_match_returns_zero",  lambda: test_update_track_no_match_returns_zero(engine)),
        ("test_update_track_empty_data",             lambda: test_update_track_empty_data(engine)),
        ("test_update_track_has_timestamp",          lambda: test_update_track_has_timestamp(engine)),
    ]

    passed = failed = 0

    for t in unit_tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1

    for name, t in integration_tests:
        drop_employees(engine)
        create_employees(engine)
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
    print(f"\n=== test_tracker  [{url}] ===")
    p, f = run_all(url)
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
