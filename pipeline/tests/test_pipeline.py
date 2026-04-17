"""
End-to-end pipeline test — runs the full INSERT → UPSERT → UPDATE cycle
against an in-memory SQLite database.

Usage::

    python -m pytest pipeline/test_pipeline.py -v
"""
from __future__ import annotations

import pytest
import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text

from crud.types import CrudConfig
from pipeline.csv_harness import PipelineRunner
from config import ensure_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def source_df():
    """Generate a 20-row test DataFrame with mixed types."""
    rng = np.random.default_rng(42)
    n = 20
    return pd.DataFrame({
        "id":       range(1, n + 1),
        "email":    [f"user{i}@test.com" for i in range(1, n + 1)],
        "name":     [f"User_{i}" for i in range(1, n + 1)],
        "score":    rng.random(n) * 100,
        "active":   rng.choice([True, False], size=n),
    })


@pytest.fixture
def engine_with_table(source_df):
    """Create an SQLite in-memory engine with the test table."""
    eng = sa.create_engine("sqlite:///:memory:")

    ensure_table(
        eng, source_df, "users",
        pk_cols=["id"],
        if_exists="drop",
    )
    # Add a UNIQUE constraint on email for upsert
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS \"users\""))
        conn.execute(text("""
            CREATE TABLE "users" (
                "id"     INTEGER NOT NULL,
                "email"  VARCHAR(255) NOT NULL,
                "name"   VARCHAR(255) NULL,
                "score"  REAL NULL,
                "active" INTEGER NULL,
                PRIMARY KEY ("id"),
                UNIQUE ("email")
            )
        """))
    return eng


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestPipelineRunner:

    def test_full_cycle(self, engine_with_table, source_df):
        """Run the complete INSERT → UPSERT → UPDATE pipeline."""
        runner = PipelineRunner(
            engine_with_table,
            source_df,
            table="users",
            pk_cols="id",
            constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=True),
        )

        report = runner.run()

        # All 3 steps should pass
        assert len(report.steps) == 3
        for step in report.steps:
            assert step.crud_result.success > 0, f"{step.step} had no successes"

        # Fingerprint should be generated
        assert report.fingerprint_before is not None
        assert report.fingerprint_after is not None

        print(report.summary())

    def test_insert_only(self, engine_with_table, source_df):
        """Run only the INSERT step."""
        runner = PipelineRunner(
            engine_with_table, source_df,
            table="users", pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
        )
        step = runner.run_insert_only()
        assert step.crud_result.success > 0
        assert step.step == "INSERT"

    def test_insert_row_count(self, engine_with_table, source_df):
        """Verify that INSERT puts the correct number of rows in the DB."""
        runner = PipelineRunner(
            engine_with_table, source_df,
            table="users", pk_cols="id", constraint_cols="email",
            config=CrudConfig(strict=False, enable_fingerprint=False),
            validate=False,
        )
        step = runner.run_insert_only()

        # 60% of 20 = 12 rows
        expected_rows = int(len(source_df) * 0.6)
        with engine_with_table.connect() as conn:
            actual = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        assert actual == expected_rows
        assert step.crud_result.success == expected_rows


class TestEnsureTable:

    def test_create_new(self, source_df):
        eng = sa.create_engine("sqlite:///:memory:")
        created = ensure_table(eng, source_df, "new_table", pk_cols=["id"])
        assert created is True

        inspector = sa.inspect(eng)
        assert inspector.has_table("new_table")

    def test_skip_existing(self, source_df):
        eng = sa.create_engine("sqlite:///:memory:")
        ensure_table(eng, source_df, "existing", pk_cols=["id"])
        created = ensure_table(eng, source_df, "existing", if_exists="skip")
        assert created is False

    def test_fail_existing(self, source_df):
        eng = sa.create_engine("sqlite:///:memory:")
        ensure_table(eng, source_df, "existing", pk_cols=["id"])
        with pytest.raises(ValueError, match="already exists"):
            ensure_table(eng, source_df, "existing", if_exists="fail")

    def test_drop_recreate(self, source_df):
        eng = sa.create_engine("sqlite:///:memory:")
        ensure_table(eng, source_df, "recreate_me", pk_cols=["id"])
        created = ensure_table(eng, source_df, "recreate_me", pk_cols=["id"], if_exists="drop")
        assert created is True
