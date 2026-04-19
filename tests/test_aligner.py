"""
Tests for aligner package (standalone modules — no engine needed)
Run: python tests/test_aligner.py --url sqlite:///:memory:
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np

from aligner.type_system import TypeFamily, infer_type_family, extract_limits, is_type_compatible
from aligner.mapping import normalize_identifier, build_column_mapping, infer_dtype_family
from aligner.policies import AlignmentPolicies, validate_policies
from aligner.models import TableSpec, ColumnSpec, ConstraintSpec
from aligner.analyze import analyze
from aligner.coercion import coerce_dataframe
from aligner.outliers import detect_outliers, apply_outlier_action
import sqlalchemy as sa

from tests.conftest import parse_url


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_table_spec(cols: dict) -> TableSpec:
    return TableSpec(schema="test", name="employees", columns=cols)

def _col(name, family, sql_type="TEXT", nullable=True, max_length=None, precision=None, scale=None):
    return ColumnSpec(
        name=name, type_family=family.value, sql_type=sql_type,
        nullable=nullable, max_length=max_length, precision=precision, scale=scale
    )


# ── type_system ───────────────────────────────────────────────────────────────

def test_infer_integer():
    assert infer_type_family(sa.Integer(), "sqlite") == TypeFamily.INTEGER

def test_infer_float():
    assert infer_type_family(sa.Float(), "sqlite") == TypeFamily.FLOAT

def test_infer_string():
    assert infer_type_family(sa.String(100), "sqlite") == TypeFamily.STRING

def test_infer_boolean():
    assert infer_type_family(sa.Boolean(), "sqlite") == TypeFamily.BOOLEAN

def test_infer_datetime():
    assert infer_type_family(sa.DateTime(), "sqlite") == TypeFamily.DATETIME

def test_infer_oracle_varchar2():
    assert infer_type_family(sa.String(50), "oracle") == TypeFamily.STRING

def test_extract_limits_string():
    limits = extract_limits(sa.String(100), "sqlite")
    assert limits["max_length"] == 100

def test_extract_limits_numeric():
    limits = extract_limits(sa.Numeric(10, 2), "sqlite")
    assert limits["precision"] == 10
    assert limits["scale"] == 2

def test_is_compatible_int_to_float():
    assert is_type_compatible(TypeFamily.INTEGER, TypeFamily.FLOAT, "sqlite")

def test_is_compatible_string_to_text():
    assert is_type_compatible(TypeFamily.STRING, TypeFamily.TEXT, "sqlite")

def test_is_not_compatible_float_to_bool():
    assert not is_type_compatible(TypeFamily.FLOAT, TypeFamily.BOOLEAN, "sqlite")

def test_is_compatible_same():
    assert is_type_compatible(TypeFamily.INTEGER, TypeFamily.INTEGER, "oracle")


# ── mapping ───────────────────────────────────────────────────────────────────

def test_normalize_identifier_oracle():
    assert normalize_identifier("emp_id", "oracle") == "EMP_ID"

def test_normalize_identifier_postgres():
    assert normalize_identifier("EMP_ID", "postgresql") == "emp_id"

def test_normalize_identifier_sqlite():
    assert normalize_identifier("EMP_ID", "sqlite") == "emp_id"

def test_build_column_mapping_exact():
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER),
        "name":   _col("name",   TypeFamily.STRING),
    })
    mappings, extra, missing = build_column_mapping(["emp_id", "name"], table_spec, dialect="sqlite")
    assert len(mappings) == 2
    assert extra == []
    assert missing == []

def test_build_column_mapping_extra_df_col():
    table_spec = _make_table_spec({"emp_id": _col("emp_id", TypeFamily.INTEGER)})
    mappings, extra, missing = build_column_mapping(["emp_id", "ghost"], table_spec, dialect="sqlite")
    assert "ghost" in extra

def test_build_column_mapping_missing_db_col():
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER),
        "name":   _col("name",   TypeFamily.STRING),
    })
    mappings, extra, missing = build_column_mapping(["emp_id"], table_spec, dialect="sqlite")
    assert "name" in missing

def test_infer_dtype_family_int():
    assert infer_dtype_family("int64") == TypeFamily.INTEGER

def test_infer_dtype_family_float():
    assert infer_dtype_family("float64") == TypeFamily.FLOAT

def test_infer_dtype_family_object():
    assert infer_dtype_family("object") == TypeFamily.STRING

def test_infer_dtype_family_bool():
    assert infer_dtype_family("bool") == TypeFamily.BOOLEAN


# ── policies ──────────────────────────────────────────────────────────────────

def test_default_policies():
    p = AlignmentPolicies()
    assert p.outliers.enabled is False
    assert p.outliers.max_pct_total_rows == 0.05
    assert p.ddl.enabled is False
    assert p.ddl.dry_run is True

def test_validate_policies_ok():
    p = AlignmentPolicies()
    validate_policies(p)  # no exception

def test_validate_policies_outlier_cap_exceeded():
    p = AlignmentPolicies()
    p.outliers.max_pct_total_rows = 0.10
    try:
        validate_policies(p)
        assert False, "should raise"
    except ValueError:
        pass


# ── analyze ───────────────────────────────────────────────────────────────────

def test_analyze_basic():
    df = pd.DataFrame([{"emp_id": 1, "name": "Alice", "salary": 90000.0}])
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER, nullable=False),
        "name":   _col("name",   TypeFamily.STRING,  max_length=100),
        "salary": _col("salary", TypeFamily.FLOAT),
    })
    report, plan, ddl_plan = analyze(df, table_spec)
    assert report.total_columns == 3
    assert report.mapped_columns == 3

def test_analyze_extra_df_column():
    df = pd.DataFrame([{"emp_id": 1, "ghost": "extra"}])
    table_spec = _make_table_spec({"emp_id": _col("emp_id", TypeFamily.INTEGER)})
    report, plan, ddl_plan = analyze(df, table_spec)
    assert "ghost" in report.extra_df_columns
    assert "ghost" in plan.drop_columns

def test_analyze_missing_db_column_not_null():
    df = pd.DataFrame([{"emp_id": 1}])
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER),
        "name":   _col("name",   TypeFamily.STRING, nullable=False),
    })
    report, plan, ddl_plan = analyze(df, table_spec)
    assert report.has_errors()

def test_analyze_null_in_not_null_col():
    df = pd.DataFrame([{"emp_id": None, "name": "Alice"}])
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER, nullable=False),
        "name":   _col("name",   TypeFamily.STRING),
    })
    report, plan, ddl_plan = analyze(df, table_spec)
    assert report.has_errors()


# ── coercion ──────────────────────────────────────────────────────────────────

def test_coerce_drops_extra_columns():
    df = pd.DataFrame([{"emp_id": 1, "name": "Alice", "ghost": "drop_me"}])
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER),
        "name":   _col("name",   TypeFamily.STRING),
    })
    _, plan, _ = analyze(df, table_spec)
    df_clean, report = coerce_dataframe(df, table_spec, plan)
    assert "ghost" not in df_clean.columns
    assert "ghost" in report.dropped_columns

def test_coerce_string_truncation():
    df = pd.DataFrame([{"emp_id": 1, "name": "A" * 200}])
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER),
        "name":   _col("name",   TypeFamily.STRING, max_length=10),
    })
    _, plan, _ = analyze(df, table_spec)
    df_clean, _ = coerce_dataframe(df, table_spec, plan)
    assert len(df_clean["name"].iloc[0]) <= 10

def test_coerce_preserves_row_count():
    df = pd.DataFrame([
        {"emp_id": 1, "name": "Alice"},
        {"emp_id": 2, "name": "Bob"},
    ])
    table_spec = _make_table_spec({
        "emp_id": _col("emp_id", TypeFamily.INTEGER),
        "name":   _col("name",   TypeFamily.STRING),
    })
    _, plan, _ = analyze(df, table_spec)
    df_clean, _ = coerce_dataframe(df, table_spec, plan)
    assert len(df_clean) == 2


# ── outliers ──────────────────────────────────────────────────────────────────

def test_detect_outliers_iqr():
    df = pd.DataFrame({"salary": [50000, 51000, 52000, 53000, 54000, 999999]})
    result = detect_outliers(df, ["salary"], method="iqr")
    assert result.outlier_rows >= 1
    assert 5 in result.outlier_indices  # last row is outlier

def test_detect_outliers_zscore():
    df = pd.DataFrame({"salary": [50000 + i*100 for i in range(20)] + [9999999]})
    result = detect_outliers(df, ["salary"], method="zscore")
    assert result.outlier_rows >= 1

def test_detect_outliers_mad():
    df = pd.DataFrame({"salary": list(range(50000, 59000, 1000)) + [999999]})
    result = detect_outliers(df, ["salary"], method="mad")
    assert result.outlier_rows >= 1

def test_detect_outliers_no_outliers():
    df = pd.DataFrame({"salary": [50000, 51000, 52000, 53000, 54000]})
    result = detect_outliers(df, ["salary"], method="iqr")
    assert result.outlier_rows == 0

def test_apply_outlier_action_drop():
    df = pd.DataFrame({"salary": [50000] * 9 + [999999]})
    result = detect_outliers(df, ["salary"], method="iqr")
    df_clean, details = apply_outlier_action(df, result, action="drop")
    assert len(df_clean) < len(df)

def test_apply_outlier_action_nullify():
    df = pd.DataFrame({"salary": [50000] * 9 + [999999]})
    result = detect_outliers(df, ["salary"], method="iqr")
    df_clean, details = apply_outlier_action(df, result, action="nullify")
    assert len(df_clean) == len(df)
    assert df_clean["salary"].isna().any()

def test_apply_outlier_action_clip():
    df = pd.DataFrame({"salary": [50000] * 9 + [999999]})
    result = detect_outliers(df, ["salary"], method="iqr")
    df_clean, details = apply_outlier_action(df, result, action="clip")
    assert df_clean["salary"].max() < 999999

def test_outlier_percentage():
    df = pd.DataFrame({"salary": [50000] * 9 + [999999]})
    result = detect_outliers(df, ["salary"], method="iqr")
    assert 0 < result.outlier_percentage <= 100


# ── runner ────────────────────────────────────────────────────────────────────

def run_all():
    tests = [
        test_infer_integer, test_infer_float, test_infer_string,
        test_infer_boolean, test_infer_datetime, test_infer_oracle_varchar2,
        test_extract_limits_string, test_extract_limits_numeric,
        test_is_compatible_int_to_float, test_is_compatible_string_to_text,
        test_is_not_compatible_float_to_bool, test_is_compatible_same,
        test_normalize_identifier_oracle, test_normalize_identifier_postgres,
        test_normalize_identifier_sqlite,
        test_build_column_mapping_exact, test_build_column_mapping_extra_df_col,
        test_build_column_mapping_missing_db_col,
        test_infer_dtype_family_int, test_infer_dtype_family_float,
        test_infer_dtype_family_object, test_infer_dtype_family_bool,
        test_default_policies, test_validate_policies_ok,
        test_validate_policies_outlier_cap_exceeded,
        test_analyze_basic, test_analyze_extra_df_column,
        test_analyze_missing_db_column_not_null, test_analyze_null_in_not_null_col,
        test_coerce_drops_extra_columns, test_coerce_string_truncation,
        test_coerce_preserves_row_count,
        test_detect_outliers_iqr, test_detect_outliers_zscore,
        test_detect_outliers_mad, test_detect_outliers_no_outliers,
        test_apply_outlier_action_drop, test_apply_outlier_action_nullify,
        test_apply_outlier_action_clip, test_outlier_percentage,
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
    print(f"\n=== test_aligner  [{url}] ===")
    p, f = run_all()
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
