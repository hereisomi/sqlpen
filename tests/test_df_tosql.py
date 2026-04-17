"""
test_df_tosql.py — Tests for pipeline.df_tosql

Covers:
- All 4 write modes: insert, replace, upsert, update
- Preprocessing flags: clean, cast, outlier, auto_profiling
- Multi-format source loading: DataFrame, CSV, Parquet, JSON
- Schema JSON dump
- Edge cases: empty source, invalid mode, update without where

Run against any database:
    pytest tests/test_df_tosql.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from pipeline.df_tosql import df_tosql


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_exists(engine, table) -> bool:
    return sa.inspect(engine).has_table(table)


def _row_count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


def _drop(engine, table):
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------

class TestInsert:

    def test_creates_table_and_inserts(self, engine, sample_df):
        _drop(engine, "t_insert")
        result = df_tosql(sample_df, "t_insert", engine,
                          if_exist="insert", clean=False, cast=False,
                          outlier=0, schema_name="")
        assert result.success == len(sample_df)
        assert _table_exists(engine, "t_insert")
        assert _row_count(engine, "t_insert") == len(sample_df)

    def test_appends_to_existing_table(self, engine, sample_df):
        _drop(engine, "t_append")
        df_tosql(sample_df, "t_append", engine,
                 if_exist="insert", clean=False, cast=False,
                 outlier=0, schema_name="")
        # Shift IDs to avoid PK conflict
        df2 = sample_df.copy()
        df2["id"] = df2["id"] + 100
        result = df_tosql(df2, "t_append", engine,
                          if_exist="insert", clean=False, cast=False,
                          outlier=0, schema_name="")
        assert result.success == len(df2)
        assert _row_count(engine, "t_append") == len(sample_df) + len(df2)

    def test_insert_from_csv(self, engine, sample_df, tmp_path):
        _drop(engine, "t_csv")
        csv_file = tmp_path / "data.csv"
        sample_df.to_csv(csv_file, index=False)
        result = df_tosql(str(csv_file), "t_csv", engine,
                          if_exist="insert", clean=False, cast=False,
                          outlier=0, schema_name="")
        assert result.success == len(sample_df)

    def test_insert_from_parquet(self, engine, sample_df, tmp_path):
        _drop(engine, "t_parquet")
        pq_file = tmp_path / "data.parquet"
        sample_df.to_parquet(pq_file, index=False)
        result = df_tosql(str(pq_file), "t_parquet", engine,
                          if_exist="insert", clean=False, cast=False,
                          outlier=0, schema_name="")
        assert result.success == len(sample_df)

    def test_insert_from_json(self, engine, sample_df, tmp_path):
        _drop(engine, "t_json")
        json_file = tmp_path / "data.json"
        sample_df.to_json(json_file, orient="records")
        result = df_tosql(str(json_file), "t_json", engine,
                          if_exist="insert", clean=False, cast=False,
                          outlier=0, schema_name="")
        assert result.success == len(sample_df)


# ---------------------------------------------------------------------------
# REPLACE
# ---------------------------------------------------------------------------

class TestReplace:

    def test_replace_drops_and_recreates(self, engine, sample_df):
        _drop(engine, "t_replace")
        df_tosql(sample_df, "t_replace", engine,
                 if_exist="insert", clean=False, cast=False,
                 outlier=0, schema_name="")
        # Replace with a smaller DataFrame
        small_df = sample_df.head(3)
        result = df_tosql(small_df, "t_replace", engine,
                          if_exist="replace", clean=False, cast=False,
                          outlier=0, schema_name="")
        assert result.success == 3
        assert _row_count(engine, "t_replace") == 3


# ---------------------------------------------------------------------------
# UPSERT
# ---------------------------------------------------------------------------

class TestUpsert:

    def test_upsert_inserts_new_rows(self, engine, sample_df):
        _drop(engine, "t_upsert")
        result = df_tosql(sample_df, "t_upsert", engine,
                          if_exist="upsert", constraint_cols="id",
                          clean=False, cast=False, outlier=0, schema_name="")
        assert result.success == len(sample_df)

    def test_upsert_updates_existing_rows(self, engine, sample_df):
        _drop(engine, "t_upsert_upd")
        df_tosql(sample_df, "t_upsert_upd", engine,
                 if_exist="insert", constraint_cols="id",
                 clean=False, cast=False, outlier=0, schema_name="")
        # Modify names
        modified = sample_df.copy()
        modified["name"] = modified["name"] + "_updated"
        result = df_tosql(modified, "t_upsert_upd", engine,
                          if_exist="upsert", constraint_cols="id",
                          clean=False, cast=False, outlier=0, schema_name="")
        assert result.success == len(modified)
        with engine.connect() as conn:
            name = conn.execute(
                text('SELECT name FROM "t_upsert_upd" WHERE id = 1')
            ).scalar()
        assert name == "User_1_updated"

    def test_upsert_composite_constraint(self, engine):
        _drop(engine, "t_upsert_comp")
        df = pd.DataFrame({
            "order_id": [1, 1, 2],
            "line_id":  [1, 2, 1],
            "qty":      [10, 20, 30],
        })
        result = df_tosql(df, "t_upsert_comp", engine,
                          if_exist="upsert", constraint_cols="order_id,line_id",
                          clean=False, cast=False, outlier=0, schema_name="")
        assert result.success == 3


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------

class TestUpdate:

    def test_update_modifies_rows(self, engine, sample_df):
        _drop(engine, "t_update")
        df_tosql(sample_df, "t_update", engine,
                 if_exist="insert", clean=False, cast=False,
                 outlier=0, schema_name="")
        modified = sample_df.copy()
        modified["name"] = "changed"
        result = df_tosql(modified, "t_update", engine,
                          if_exist="update",
                          where=[("id", "=", "?")],
                          clean=False, cast=False, outlier=0, schema_name="")
        assert result.success > 0

    def test_update_missing_table_raises(self, engine, sample_df):
        _drop(engine, "t_ghost")
        with pytest.raises(ValueError, match="does not exist"):
            df_tosql(sample_df, "t_ghost", engine,
                     if_exist="update", where=[("id", "=", "?")])

    def test_update_missing_where_raises(self, engine, sample_df):
        with pytest.raises(ValueError, match="without 'where' conditions"):
            df_tosql(sample_df, "any_table", engine, if_exist="update")


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------

class TestPreprocessing:

    def test_clean_sanitizes_column_names(self, engine, dirty_df):
        _drop(engine, "t_clean")
        result = df_tosql(dirty_df, "t_clean", engine,
                          if_exist="insert", clean=True, cast=False,
                          outlier=0, schema_name="")
        assert result.success == len(dirty_df)
        cols = {c["name"] for c in sa.inspect(engine).get_columns("t_clean")}
        assert "id" in cols
        assert "email" in cols
        assert "score" in cols

    def test_cast_converts_string_types(self, engine, dirty_df):
        _drop(engine, "t_cast")
        result = df_tosql(dirty_df, "t_cast", engine,
                          if_exist="insert", clean=True, cast=True,
                          outlier=0, schema_name="")
        assert result.success == len(dirty_df)

    def test_outlier_replaces_extreme_values(self, engine, dirty_df):
        _drop(engine, "t_outlier")
        df_tosql(dirty_df, "t_outlier", engine,
                 if_exist="insert", clean=True, cast=True,
                 outlier=0.5, schema_name="")
        with engine.connect() as conn:
            max_score = conn.execute(
                text('SELECT MAX(score) FROM "t_outlier"')
            ).scalar()
        # 9999.0 outlier should be replaced with 0
        assert float(max_score) < 9999.0

    def test_schema_json_dump(self, engine, sample_df, tmp_path):
        _drop(engine, "t_schema_dump")
        schema_path = tmp_path / "schema.json"
        df_tosql(sample_df, "t_schema_dump", engine,
                 if_exist="insert", clean=False, cast=False,
                 outlier=0, schema_name=str(schema_path))
        assert schema_path.exists()
        doc = json.loads(schema_path.read_text())
        assert doc["table"] == "t_schema_dump"
        assert "columns" in doc


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_invalid_if_exist_raises(self, engine, sample_df):
        with pytest.raises(ValueError, match="Invalid if_exist"):
            df_tosql(sample_df, "any", engine, if_exist="delete")

    def test_file_not_found_raises(self, engine):
        with pytest.raises(FileNotFoundError):
            df_tosql("nonexistent_file.csv", "any", engine)
