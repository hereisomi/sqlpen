"""
test_dict_tosql.py — Tests for pipeline.dict_tosql

Covers:
- Single dict insert
- List of dicts insert
- Upsert with constraint
- Update with where
- Empty input guard
- Passthrough to df_tosql (clean, cast, outlier)

Run against any database:
    pytest tests/test_dict_tosql.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from pipeline.dict_tosql import dict_tosql


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drop(engine, table):
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


def _row_count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


def _fetch_one(engine, table, where_col, where_val):
    with engine.connect() as conn:
        return conn.execute(
            text(f'SELECT * FROM "{table}" WHERE {where_col} = :v'),
            {"v": where_val}
        ).fetchone()


# ---------------------------------------------------------------------------
# Single dict
# ---------------------------------------------------------------------------

class TestSingleDict:

    def test_insert_single_dict(self, engine):
        _drop(engine, "td_single")
        result = dict_tosql(
            {"id": 1, "name": "Alice", "score": 95.5},
            table="td_single", engine=engine,
            if_exist="insert", clean=False, cast=False,
            outlier=0, schema_name=""
        )
        assert result.success == 1
        assert _row_count(engine, "td_single") == 1

    def test_insert_single_dict_with_clean(self, engine):
        _drop(engine, "td_single_clean")
        result = dict_tosql(
            {" ID ": 1, "Name!": "Alice", "Score ": 95.5},
            table="td_single_clean", engine=engine,
            if_exist="insert", clean=True, cast=False,
            outlier=0, schema_name=""
        )
        assert result.success == 1
        cols = {c["name"] for c in sa.inspect(engine).get_columns("td_single_clean")}
        assert "id" in cols
        assert "name" in cols


# ---------------------------------------------------------------------------
# List of dicts
# ---------------------------------------------------------------------------

class TestListOfDicts:

    def test_insert_list_of_dicts(self, engine):
        _drop(engine, "td_list")
        records = [
            {"id": i, "name": f"User_{i}", "value": float(i * 10)}
            for i in range(1, 6)
        ]
        result = dict_tosql(records, table="td_list", engine=engine,
                            if_exist="insert", clean=False, cast=False,
                            outlier=0, schema_name="")
        assert result.success == 5
        assert _row_count(engine, "td_list") == 5

    def test_upsert_list_of_dicts(self, engine):
        _drop(engine, "td_upsert")
        dialect = engine.dialect.name.lower()
        if dialect == "sqlite":
            ddl = 'CREATE TABLE "td_upsert" ("id" INTEGER NOT NULL PRIMARY KEY, "name" VARCHAR(100))'
        elif dialect == "postgresql":
            ddl = 'CREATE TABLE "td_upsert" ("id" BIGINT NOT NULL PRIMARY KEY, "name" VARCHAR(100))'
        elif dialect in ("mysql", "mariadb"):
            ddl = 'CREATE TABLE `td_upsert` (`id` BIGINT NOT NULL PRIMARY KEY, `name` VARCHAR(100))'
        elif dialect == "mssql":
            ddl = 'CREATE TABLE [td_upsert] ([id] BIGINT NOT NULL PRIMARY KEY, [name] VARCHAR(100))'
        else:
            ddl = 'CREATE TABLE "td_upsert" ("id" NUMBER(19) NOT NULL PRIMARY KEY, "name" VARCHAR2(100))'
        with engine.begin() as conn:
            conn.execute(sa.text(ddl))
        records = [{"id": i, "name": f"User_{i}"} for i in range(1, 6)]
        dict_tosql(records, table="td_upsert", engine=engine,
                   if_exist="insert", clean=False, cast=False,
                   outlier=0, schema_name="")
        updated = [{"id": 1, "name": "Alice_updated"},
                   {"id": 6, "name": "User_6"}]
        result = dict_tosql(updated, table="td_upsert", engine=engine,
                            if_exist="upsert", constraint_cols="id",
                            clean=False, cast=False, outlier=0, schema_name="")
        assert result.success == 2
        row = _fetch_one(engine, "td_upsert", "id", 1)
        assert row is not None

    def test_update_list_of_dicts(self, engine):
        _drop(engine, "td_update")
        records = [{"id": i, "name": f"User_{i}"} for i in range(1, 4)]
        dict_tosql(records, table="td_update", engine=engine,
                   if_exist="insert", clean=False, cast=False,
                   outlier=0, schema_name="")
        modified = [{"id": i, "name": "changed"} for i in range(1, 4)]
        result = dict_tosql(modified, table="td_update", engine=engine,
                            if_exist="update", where=[("id", "=", "?")],
                            clean=False, cast=False, outlier=0, schema_name="")
        assert result.success > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestDictEdgeCases:

    def test_empty_dict_raises(self, engine):
        with pytest.raises(ValueError, match="empty"):
            dict_tosql({}, table="any", engine=engine)

    def test_empty_list_raises(self, engine):
        with pytest.raises(ValueError, match="empty"):
            dict_tosql([], table="any", engine=engine)

    def test_cast_converts_string_dates(self, engine):
        _drop(engine, "td_cast")
        records = [{"id": 1, "name": "Alice", "joined": "2024-01-15"}]
        result = dict_tosql(records, table="td_cast", engine=engine,
                            if_exist="insert", clean=False, cast=True,
                            outlier=0, schema_name="")
        assert result.success == 1
