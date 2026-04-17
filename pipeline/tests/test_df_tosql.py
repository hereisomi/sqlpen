"""
Unit tests for pipeline.tosql facade

Tests the complete df_tosql orchestration covering:
- Table not exists vs Table exists
- replace | insert | upsert | update paths
- Core parameter wiring (clean, cast, profiling, outlier)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text

from pipeline.df_tosql import df_tosql


@pytest.fixture
def test_df():
    """Generates a small test dataframe that needs type inference and cleaning."""
    return pd.DataFrame({
        " ID ": ["1", "2", "3"],          # Spaces in name, strings for ints
        "EMAIL!": ["a@test", "b@test", "c@test"], # Dirty char
        " score ": [95.5, 10.0, 999.0],   # Extreme outlier (999.0)
        "dt": ["2020-01-01", "2021-01-01", "2022-01-01"], # string dates
    })


@pytest.fixture
def memory_engine():
    """Returns a fresh memory engine."""
    return sa.create_engine("sqlite:///:memory:")


class TestDfToSql:
    def test_insert_from_scratch(self, test_df, memory_engine, tmp_path):
        """Test inserting into a non-existent table builds schema and loads."""
        schema_path = tmp_path / "schema.json"
        
        result = df_tosql(
            df=test_df,
            table="users",
            engine=memory_engine,
            if_exist="insert",
            constraint_cols=[" ID "],
            clean=True,
            cast=True,
            outlier=0.5,
            schema_name=str(schema_path)
        )
        
        assert result.success == 3
        
        # Verify cleaning
        inspector = sa.inspect(memory_engine)
        assert inspector.has_table("users")
        columns = {c["name"] for c in inspector.get_columns("users")}
        
        # " ID " -> "id", "EMAIL!" -> "email"
        assert "id" in columns
        assert "email" in columns
        
        # Verify schema dump
        assert schema_path.exists()
        doc = json.loads(schema_path.read_text())
        assert doc["table"] == "users"
        
        # Verify outlier replaced -> 999 is replaced internally by 0.0 because of harsh threshold
        with memory_engine.connect() as conn:
            # We expect the 999.0 score to be muted
            row3_score = conn.execute(text("SELECT score FROM users WHERE id=3")).scalar()
            assert row3_score == 0.0

    def test_upsert_existing_table(self, test_df, memory_engine):
        """Test upserting updates existing rows on conflict."""
        # Insert initial data
        df_tosql(test_df, "users", memory_engine, if_exist="insert", 
                 constraint_cols=" ID ", clean=True, outlier=0.0, schema_name="")
        
        # Modify DataFrame: User 2 gets new email
        df_mod = test_df.copy()
        df_mod.loc[1, "EMAIL!"] = "b_new@test"
        
        # Upsert
        result = df_tosql(
            df=df_mod,
            table="users",
            engine=memory_engine,
            if_exist="upsert",
            constraint_cols=" ID ",  # Will remap to 'id' if clean=True
            clean=True,
            cast=True,
            outlier=0.0,
            schema_name=""
        )
        
        # 3 rows upserted
        assert result.success == 3
        
        with memory_engine.connect() as conn:
            email2 = conn.execute(text("SELECT email FROM users WHERE id=2")).scalar()
            assert email2 == "b_new@test"

    def test_update_missing_table_fails(self, test_df, memory_engine):
        with pytest.raises(ValueError, match="does not exist"):
            df_tosql(test_df, "ghost_table", memory_engine, if_exist="update", where=[("id", "=", "?")])

    def test_update_missing_where_fails(self, test_df, memory_engine):
        with pytest.raises(ValueError, match="without 'where' conditions"):
            df_tosql(test_df, "users", memory_engine, if_exist="update")
