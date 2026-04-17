"""
test_harness.py — Tests for pipeline.csv_harness

Covers:
- Full INSERT → UPSERT → UPDATE cycle
- Individual step execution
- Preprocessing flags (clean, cast, outlier, auto_profiling)
- Schema fingerprint before/after
- Schema drift detection
- Report file generation
- run_csv_pipeline convenience function
- Minimum row guard (< 5 rows)

Run against any database:
    pytest tests/test_harness.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from crud.types import CrudConfig
from pipeline.csv_harness import PipelineRunner, PipelineReport, run_csv_pipeline
from config import ensure_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def harness_df():
    """20-row DataFrame suitable for harness slicing (needs >= 5 rows)."""
    rng = np.random.default_rng(42)
    n = 20
    return pd.DataFrame({
        "id":    range(1, n + 1),
        "email": [f"user{i}@test.com" for i in range(1, n + 1)],
        "name":  [f"User_{i}" for i in range(1, n + 1)],
        "score": rng.random(n) * 100,
    })


@pytest.fixture
def dirty_harness_df():
    """20-row DataFrame with dirty column names for preprocessing tests."""
    rng = np.random.default_rng(7)
    n = 20
    return pd.DataFrame({
        " ID ":    range(1, n + 1),
        "Email!":  [f"user{i}@test.com" for i in range(1, n + 1)],
        " Name ":  [f"User_{i}" for i in range(1, n + 1)],
        "Score":   [float(i) if i < n else 9999.0 for i in range(1, n + 1)],
    })


@pytest.fixture
def harness_engine(engine, harness_df):
    """Engine with the harness test table pre-created with UNIQUE on email."""
    dialect = engine.dialect.name.lower()

    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS "harness_users"'))

    if dialect == "sqlite":
        ddl = """
            CREATE TABLE "harness_users" (
                "id"    INTEGER NOT NULL PRIMARY KEY,
                "email" VARCHAR(255) NOT NULL UNIQUE,
                "name"  VARCHAR(255),
                "score" REAL
            )
        """
    elif dialect == "postgresql":
        ddl = """
            CREATE TABLE "harness_users" (
                "id"    BIGINT NOT NULL PRIMARY KEY,
                "email" VARCHAR(255) NOT NULL UNIQUE,
                "name"  VARCHAR(255),
                "score" DOUBLE PRECISION
            )
        """
    elif dialect in ("mysql", "mariadb"):
        ddl = """
            CREATE TABLE `harness_users` (
                `id`    BIGINT NOT NULL PRIMARY KEY,
                `email` VARCHAR(255) NOT NULL UNIQUE,
                `name`  VARCHAR(255),
                `score` DOUBLE
            )
        """
    elif dialect == "mssql":
        ddl = """
            CREATE TABLE [harness_users] (
                [id]    BIGINT NOT NULL PRIMARY KEY,
                [email] VARCHAR(255) NOT NULL UNIQUE,
                [name]  VARCHAR(255),
                [score] FLOAT
            )
        """
    else:
        ddl = """
            CREATE TABLE "harness_users" (
                "id"    NUMBER(19) NOT NULL PRIMARY KEY,
                "email" VARCHAR2(255) NOT NULL UNIQUE,
                "name"  VARCHAR2(255),
                "score" NUMBER
            )
        """

    with engine.begin() as conn:
        conn.execute(text(ddl))

    yield engine

    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS "harness_users"'))


# ---------------------------------------------------------------------------
# Full cycle
# ---------------------------------------------------------------------------

class TestFullCycle:

    def test_all_steps_run(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=True),
            clean=False, cast=False, outlier=0,
        )
        report = runner.run()
        assert isinstance(report, PipelineReport)
        assert len(report.steps) == 3
        assert [s.step for s in report.steps] == ["INSERT", "UPSERT", "UPDATE"]

    def test_all_steps_have_successes(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=False, cast=False, outlier=0,
        )
        report = runner.run()
        for step in report.steps:
            assert step.crud_result.success > 0, f"{step.step} had 0 successes"

    def test_total_elapsed_recorded(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=False, cast=False, outlier=0,
        )
        report = runner.run()
        assert report.total_elapsed_s > 0


# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------

class TestIndividualSteps:

    def test_insert_only(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=False, cast=False, outlier=0,
        )
        step = runner.run_insert_only()
        assert step.step == "INSERT"
        assert step.crud_result.success > 0
        # 60% of 20 = 12 rows
        assert step.rows_sent == int(len(harness_df) * 0.6)

    def test_insert_row_count_in_db(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=False, cast=False, outlier=0, validate=False,
        )
        runner.run_insert_only()
        with harness_engine.connect() as conn:
            count = conn.execute(text('SELECT COUNT(*) FROM "harness_users"')).scalar()
        assert count == int(len(harness_df) * 0.6)


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

class TestHarnessPreprocessing:

    def test_clean_remaps_pk_and_constraint(self, engine, dirty_harness_df):
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE IF EXISTS "harness_dirty"'))
        runner = PipelineRunner(
            engine, dirty_harness_df,
            table="harness_dirty",
            pk_cols=" ID ", constraint_cols="Email!",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=True, cast=False, outlier=0,
        )
        # pk and constraint should be remapped through sanitizer
        assert runner.harness.pk == ["id"]
        assert runner.harness.constraint == ["email"]
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE IF EXISTS "harness_dirty"'))

    def test_outlier_applied_before_slicing(self, engine, dirty_harness_df):
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE IF EXISTS "harness_outlier"'))
        runner = PipelineRunner(
            engine, dirty_harness_df,
            table="harness_outlier",
            pk_cols=" ID ", constraint_cols="Email!",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=True, cast=True, outlier=0.5,
        )
        # 9999.0 should be replaced with 0 in the prepared df
        max_score = runner.harness.df["score"].max()
        assert max_score < 9999.0
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE IF EXISTS "harness_outlier"'))


# ---------------------------------------------------------------------------
# Schema fingerprint
# ---------------------------------------------------------------------------

class TestSchemaFingerprint:

    def test_fingerprint_captured(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=True),
            clean=False, cast=False, outlier=0,
        )
        report = runner.run()
        assert report.fingerprint_before is not None
        assert report.fingerprint_after is not None
        assert len(report.fingerprint_before.hash) > 0

    def test_no_schema_drift_on_clean_run(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=True),
            clean=False, cast=False, outlier=0,
        )
        report = runner.run()
        if report.schema_diff:
            assert not report.schema_diff.changed


# ---------------------------------------------------------------------------
# Report file
# ---------------------------------------------------------------------------

class TestReportFile:

    def test_report_written_to_custom_dir(self, harness_engine, harness_df, tmp_path):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=False, cast=False, outlier=0,
            report_path=tmp_path / "harness_users_harness.txt",
        )
        runner.run()
        report_file = tmp_path / "harness_users_harness.txt"
        assert report_file.exists()
        content = report_file.read_text()
        assert "SQLPEN HARNESS REPORT" in content
        assert "VERDICT" in content

    def test_report_summary_contains_steps(self, harness_engine, harness_df):
        runner = PipelineRunner(
            harness_engine, harness_df,
            table="harness_users",
            pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            clean=False, cast=False, outlier=0,
        )
        report = runner.run()
        summary = report.summary()
        assert "INSERT" in summary
        assert "UPSERT" in summary
        assert "UPDATE" in summary


# ---------------------------------------------------------------------------
# run_csv_pipeline convenience function
# ---------------------------------------------------------------------------

class TestRunCsvPipeline:

    def test_run_csv_pipeline(self, harness_engine, harness_df, tmp_path):
        csv_file = tmp_path / "harness_data.csv"
        harness_df.to_csv(csv_file, index=False)

        report = run_csv_pipeline(
            str(csv_file), harness_engine,
            table="harness_users",
            pk_cols="id", constraint_cols="id",
            clean=False, cast=False, outlier=0,
            report_dir=tmp_path,
        )
        assert isinstance(report, PipelineReport)
        assert len(report.steps) == 3
        report_file = tmp_path / "harness_data_harness.txt"
        assert report_file.exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestHarnessEdgeCases:

    def test_too_few_rows_raises(self, engine):
        tiny_df = pd.DataFrame({
            "id": [1, 2, 3],
            "name": ["a", "b", "c"],
        })
        with pytest.raises(ValueError, match="at least 5 rows"):
            PipelineRunner(
                engine, tiny_df,
                table="any", pk_cols="id", constraint_cols="id",
            )

    def test_missing_pk_column_raises(self, engine, harness_df):
        with pytest.raises(ValueError, match="Missing PK columns"):
            PipelineRunner(
                engine, harness_df,
                table="any", pk_cols="nonexistent_col", constraint_cols="email",
            )

    def test_all_columns_are_keys_raises(self, engine):
        df = pd.DataFrame({"id": range(1, 11), "email": [f"u{i}@t.com" for i in range(1, 11)]})
        with pytest.raises(ValueError, match="No mutable columns"):
            PipelineRunner(
                engine, df,
                table="any", pk_cols="id", constraint_cols="email",
            )
