"""
Unit tests for crud.schema — SchemaAligner and supporting components.

All tests use SQLite in-memory so no external database is required.
"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
import sqlalchemy as sa
from sqlalchemy import text

from crud.schema import SchemaAligner, ColumnInfo
from crud.types import CrudConfig
from crud.fingerprint import build_fingerprint, diff_fingerprints


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def engine():
    """In-memory SQLite engine with a pre-built test table."""
    eng = sa.create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE test_tbl (
                id      INTEGER PRIMARY KEY,
                name    VARCHAR(50) NOT NULL,
                score   REAL,
                active  BOOLEAN,
                notes   TEXT
            )
        """))
    return eng


@pytest.fixture
def numeric_engine():
    """Engine with a table that has a NUMERIC(8,2) column."""
    eng = sa.create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE ledger (
                txn_id  INTEGER PRIMARY KEY,
                amount  NUMERIC(8, 2) NOT NULL,
                label   VARCHAR(20)
            )
        """))
    return eng


# ---------------------------------------------------------------------------
# 1. Column name alignment
# ---------------------------------------------------------------------------
class TestColumnAlignment:

    def test_case_insensitive_match(self, engine):
        df = pd.DataFrame({"ID": [1], "NAME": ["Alice"], "SCORE": [9.5]})
        aligner = SchemaAligner(engine, config=CrudConfig(strict=False, enable_fingerprint=False))
        result = aligner.align(df, "test_tbl")
        # Columns should be renamed to match DB casing
        assert "id" in result.columns
        assert "name" in result.columns

    def test_bom_strip(self, engine):
        df = pd.DataFrame({"\ufeffid": [2], "name": ["Bob"], "score": [8.0]})
        aligner = SchemaAligner(engine, config=CrudConfig(strict=False, enable_fingerprint=False))
        result = aligner.align(df, "test_tbl")
        assert "id" in result.columns

    def test_extra_columns_dropped(self, engine):
        df = pd.DataFrame({"id": [3], "name": ["Eve"], "score": [7.0], "extra_col": [42]})
        aligner = SchemaAligner(engine, config=CrudConfig(strict=False, enable_fingerprint=False))
        result = aligner.align(df, "test_tbl")
        assert "extra_col" not in result.columns

    def test_drop_extra_cols_false_raises(self, engine):
        df = pd.DataFrame({"id": [3], "name": ["Eve"], "extra_col": [42]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, drop_extra_cols=False, enable_fingerprint=False,
        ))
        with pytest.raises(ValueError, match="Extra columns"):
            aligner.align(df, "test_tbl")


# ---------------------------------------------------------------------------
# 2. Integer coercion
# ---------------------------------------------------------------------------
class TestIntegerCoercion:

    def test_valid_integers(self, engine):
        df = pd.DataFrame({"id": [10, 20], "name": ["A", "B"]})
        aligner = SchemaAligner(engine, config=CrudConfig(strict=False, enable_fingerprint=False))
        result = aligner.align(df, "test_tbl")
        assert result["id"].tolist() == [10, 20]

    def test_string_integers_coerced(self, engine):
        df = pd.DataFrame({"id": ["10", "20"], "name": ["A", "B"]})
        aligner = SchemaAligner(engine, config=CrudConfig(strict=False, enable_fingerprint=False))
        result = aligner.align(df, "test_tbl")
        # After _finalize_types, values become native Python objects
        assert result["id"].tolist() == [10, 20]


# ---------------------------------------------------------------------------
# 3. String truncation (char-length and byte-length)
# ---------------------------------------------------------------------------
class TestStringTruncation:

    def test_char_length_enforcement(self, engine):
        """name column is VARCHAR(50) — strings longer than 50 chars should be NULLed."""
        long_name = "x" * 60
        df = pd.DataFrame({"id": [1], "name": [long_name]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, on_error="coerce", enable_fingerprint=False,
        ))
        result = aligner.align(df, "test_tbl")
        assert result["name"].iloc[0] is None

    def test_byte_length_enforcement(self, engine):
        """With byte_semantics=True, multi-byte chars use more bytes than chars."""
        # "café" = 5 chars but 6 bytes in UTF-8 (é is 2 bytes)
        # For a VARCHAR(5) column this would pass char-check but fail byte-check.
        # Our test table has VARCHAR(50), so we need a string with byte-length > 50.
        # Use 26 2-byte characters = 52 bytes but only 26 chars
        multibyte = "é" * 26  # 26 chars, 52 bytes
        df = pd.DataFrame({"id": [1], "name": [multibyte]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, on_error="coerce", byte_semantics=True, enable_fingerprint=False,
        ))
        result = aligner.align(df, "test_tbl")
        # 52 bytes > 50 limit → should be nullified
        assert result["name"].iloc[0] is None


# ---------------------------------------------------------------------------
# 4. Precision / scale enforcement
# ---------------------------------------------------------------------------
class TestPrecisionScale:

    def test_scale_rounding(self, numeric_engine):
        """NUMERIC(8,2) — values should be rounded to 2 decimal places."""
        df = pd.DataFrame({"txn_id": [1], "amount": [123.456], "label": ["test"]})
        aligner = SchemaAligner(numeric_engine, config=CrudConfig(
            strict=False, enable_fingerprint=False,
        ))
        result = aligner.align(df, "ledger")
        # 123.456 rounded to 2 decimal places = 123.46
        assert result["amount"].iloc[0] == pytest.approx(123.46)

    def test_precision_overflow_nullified(self, numeric_engine):
        """NUMERIC(8,2) allows max 6 integer digits. 1234567.89 overflows."""
        df = pd.DataFrame({"txn_id": [1], "amount": [1234567.89], "label": ["big"]})
        aligner = SchemaAligner(numeric_engine, config=CrudConfig(
            strict=False, on_error="coerce", enable_fingerprint=False,
        ))
        result = aligner.align(df, "ledger")
        # 7 integer digits > max 6 → NaN → finalized to None
        assert result["amount"].iloc[0] is None


# ---------------------------------------------------------------------------
# 5. NOT NULL enforcement
# ---------------------------------------------------------------------------
class TestNullability:

    def test_not_null_warning(self, engine):
        """name is NOT NULL — passing None should trigger failure rate check."""
        df = pd.DataFrame({"id": [1], "name": [None], "score": [5.0]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, on_error="coerce", enable_fingerprint=False,
        ))
        # Should not raise, just log warning (on_error=coerce)
        result = aligner.align(df, "test_tbl")
        assert pd.isna(result["name"].iloc[0]) or result["name"].iloc[0] is None


# ---------------------------------------------------------------------------
# 6. Schema fingerprint generation and diff
# ---------------------------------------------------------------------------
class TestFingerprint:

    def test_fingerprint_generated(self, engine):
        df = pd.DataFrame({"id": [1], "name": ["test"]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, enable_fingerprint=True,
        ))
        aligner.align(df, "test_tbl")
        fp = aligner.get_fingerprint()
        assert fp is not None
        assert len(fp.hash) == 16
        assert len(fp.columns) == 5  # id, name, score, active, notes

    def test_fingerprint_disabled(self, engine):
        df = pd.DataFrame({"id": [1], "name": ["test"]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, enable_fingerprint=False,
        ))
        aligner.align(df, "test_tbl")
        assert aligner.get_fingerprint() is None

    def test_diff_detects_added_column(self, engine):
        inspector = sa.inspect(engine)
        fp_before = build_fingerprint(inspector, "test_tbl")

        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE test_tbl ADD COLUMN new_col TEXT"))

        # Need a fresh inspector to see the new column
        inspector2 = sa.inspect(engine)
        fp_after = build_fingerprint(inspector2, "test_tbl")

        diff = diff_fingerprints(fp_before, fp_after)
        assert diff.changed
        assert "new_col" in diff.added

    def test_diff_unchanged(self, engine):
        inspector = sa.inspect(engine)
        fp1 = build_fingerprint(inspector, "test_tbl")
        fp2 = build_fingerprint(inspector, "test_tbl")
        diff = diff_fingerprints(fp1, fp2)
        assert not diff.changed


# ---------------------------------------------------------------------------
# 7. Configurable drop policy
# ---------------------------------------------------------------------------
class TestDropPolicy:

    def test_drop_true_silently_removes(self, engine):
        df = pd.DataFrame({"id": [1], "name": ["X"], "phantom": [99]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, drop_extra_cols=True, enable_fingerprint=False,
        ))
        result = aligner.align(df, "test_tbl")
        assert "phantom" not in result.columns

    def test_drop_false_raises_valueerror(self, engine):
        df = pd.DataFrame({"id": [1], "name": ["X"], "phantom": [99]})
        aligner = SchemaAligner(engine, config=CrudConfig(
            strict=False, drop_extra_cols=False, enable_fingerprint=False,
        ))
        with pytest.raises(ValueError, match="Extra columns"):
            aligner.align(df, "test_tbl")
