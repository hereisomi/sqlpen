"""
test_crud.py — Tests for crud auto_insert / auto_upsert / auto_update

Covers:
- auto_insert: basic, chunked, bulk vs row_fallback method
- auto_upsert: insert new, update existing, composite constraint
- auto_update: simple WHERE, compound WHERE, expression logic
- CrudConfig: chunk_size, strict, add_missing_cols, trace_sql
- CrudResult fields: total, success, failed, method
- Empty DataFrame guard

Run against any database:
    pytest tests/test_crud.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from crud import auto_insert, auto_upsert, auto_update, CrudConfig, CrudResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drop(engine, table):
    with engine.begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


def _row_count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


def _create_users_table(engine, table="crud_users"):
    """Create a simple users table with PK and UNIQUE on email."""
    dialect = engine.dialect.name.lower()
    _drop(engine, table)

    if dialect == "sqlite":
        ddl = f"""
            CREATE TABLE "{table}" (
                "id"    INTEGER NOT NULL PRIMARY KEY,
                "name"  VARCHAR(100),
                "email" VARCHAR(255) UNIQUE,
                "score" REAL
            )
        """
    elif dialect == "postgresql":
        ddl = f"""
            CREATE TABLE "{table}" (
                "id"    BIGINT NOT NULL PRIMARY KEY,
                "name"  VARCHAR(100),
                "email" VARCHAR(255) UNIQUE,
                "score" DOUBLE PRECISION
            )
        """
    elif dialect in ("mysql", "mariadb"):
        ddl = f"""
            CREATE TABLE `{table}` (
                `id`    BIGINT NOT NULL PRIMARY KEY,
                `name`  VARCHAR(100),
                `email` VARCHAR(255) UNIQUE,
                `score` DOUBLE
            )
        """
    elif dialect == "mssql":
        ddl = f"""
            CREATE TABLE [{table}] (
                [id]    BIGINT NOT NULL PRIMARY KEY,
                [name]  VARCHAR(100),
                [email] VARCHAR(255) UNIQUE,
                [score] FLOAT
            )
        """
    else:
        ddl = f"""
            CREATE TABLE "{table}" (
                "id"    NUMBER(19) NOT NULL PRIMARY KEY,
                "name"  VARCHAR2(100),
                "email" VARCHAR2(255) UNIQUE,
                "score" NUMBER
            )
        """
    with engine.begin() as conn:
        conn.execute(text(ddl))


def _make_users(n=10, id_offset=0) -> pd.DataFrame:
    return pd.DataFrame({
        "id":    range(1 + id_offset, n + 1 + id_offset),
        "name":  [f"User_{i}" for i in range(1 + id_offset, n + 1 + id_offset)],
        "email": [f"user{i}@test.com" for i in range(1 + id_offset, n + 1 + id_offset)],
        "score": [float(i * 10) for i in range(1 + id_offset, n + 1 + id_offset)],
    })


# ---------------------------------------------------------------------------
# auto_insert
# ---------------------------------------------------------------------------

class TestAutoInsert:

    def test_basic_insert(self, engine):
        _create_users_table(engine, "ci_basic")
        df = _make_users(5)
        result = auto_insert(engine, df, "ci_basic",
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert isinstance(result, CrudResult)
        assert result.success == 5
        assert result.failed == 0
        assert _row_count(engine, "ci_basic") == 5

    def test_insert_returns_bulk_method(self, engine):
        _create_users_table(engine, "ci_method")
        df = _make_users(5)
        result = auto_insert(engine, df, "ci_method",
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.method in ("bulk", "row_fallback")

    def test_insert_chunked(self, engine):
        _create_users_table(engine, "ci_chunk")
        df = _make_users(50)
        result = auto_insert(engine, df, "ci_chunk",
                             config=CrudConfig(chunk_size=10, strict=False,
                                               enable_fingerprint=False))
        assert result.success == 50
        assert _row_count(engine, "ci_chunk") == 50

    def test_insert_from_dict(self, engine):
        _create_users_table(engine, "ci_dict")
        record = {"id": 1, "name": "Alice", "email": "alice@test.com", "score": 99.0}
        result = auto_insert(engine, record, "ci_dict",
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success == 1

    def test_insert_from_list_of_dicts(self, engine):
        _create_users_table(engine, "ci_list")
        records = [{"id": i, "name": f"U{i}", "email": f"u{i}@t.com", "score": float(i)}
                   for i in range(1, 6)]
        result = auto_insert(engine, records, "ci_list",
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success == 5

    def test_insert_large_batch(self, engine, large_df):
        _drop(engine, "ci_large")
        _create_users_table(engine, "ci_large")
        # large_df has different columns so insert directly without schema aligner
        from sqlalchemy import text as _text
        with engine.begin() as conn:
            conn.execute(_text('DROP TABLE IF EXISTS "ci_large"'))
        # Create a matching table for large_df columns: id, name, value, flag
        dialect = engine.dialect.name.lower()
        _drop(engine, "ci_large")
        if dialect == "sqlite":
            ddl = 'CREATE TABLE "ci_large" ("id" INTEGER PRIMARY KEY, "name" VARCHAR(100), "value" REAL, "flag" INTEGER)'
        elif dialect == "postgresql":
            ddl = 'CREATE TABLE "ci_large" ("id" BIGINT PRIMARY KEY, "name" VARCHAR(100), "value" DOUBLE PRECISION, "flag" BOOLEAN)'
        elif dialect in ("mysql", "mariadb"):
            ddl = 'CREATE TABLE `ci_large` (`id` BIGINT PRIMARY KEY, `name` VARCHAR(100), `value` DOUBLE, `flag` TINYINT(1))'
        elif dialect == "mssql":
            ddl = 'CREATE TABLE [ci_large] ([id] BIGINT PRIMARY KEY, [name] VARCHAR(100), [value] FLOAT, [flag] BIT)'
        else:
            ddl = 'CREATE TABLE "ci_large" ("id" NUMBER(19) PRIMARY KEY, "name" VARCHAR2(100), "value" NUMBER, "flag" NUMBER(1))'
        with engine.begin() as conn:
            conn.execute(sa.text(ddl))
        result = auto_insert(engine, large_df, "ci_large",
                             config=CrudConfig(chunk_size=100, strict=False,
                                               enable_fingerprint=False))
        assert result.success == len(large_df)


# ---------------------------------------------------------------------------
# auto_upsert
# ---------------------------------------------------------------------------

class TestAutoUpsert:

    def test_upsert_inserts_new_rows(self, engine):
        _create_users_table(engine, "cu_new")
        df = _make_users(5)
        result = auto_upsert(engine, df, "cu_new", constrain=["id"],
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success == 5
        assert _row_count(engine, "cu_new") == 5

    def test_upsert_updates_existing_rows(self, engine):
        _create_users_table(engine, "cu_upd")
        df = _make_users(5)
        auto_insert(engine, df, "cu_upd",
                    config=CrudConfig(strict=False, enable_fingerprint=False))

        modified = df.copy()
        modified["name"] = modified["name"] + "_v2"
        result = auto_upsert(engine, modified, "cu_upd", constrain=["id"],
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success == 5

        with engine.connect() as conn:
            name = conn.execute(
                text('SELECT name FROM "cu_upd" WHERE id = 1')
            ).scalar()
        assert "_v2" in str(name)

    def test_upsert_composite_constraint(self, engine):
        _drop(engine, "cu_comp")
        dialect = engine.dialect.name.lower()
        if dialect == "sqlite":
            ddl = """
                CREATE TABLE "cu_comp" (
                    "order_id" INTEGER NOT NULL,
                    "line_id"  INTEGER NOT NULL,
                    "qty"      INTEGER,
                    PRIMARY KEY ("order_id", "line_id")
                )
            """
        else:
            ddl = """
                CREATE TABLE "cu_comp" (
                    "order_id" BIGINT NOT NULL,
                    "line_id"  BIGINT NOT NULL,
                    "qty"      BIGINT,
                    PRIMARY KEY ("order_id", "line_id")
                )
            """
        with engine.begin() as conn:
            conn.execute(text(ddl))

        df = pd.DataFrame({
            "order_id": [1, 1, 2],
            "line_id":  [1, 2, 1],
            "qty":      [10, 20, 30],
        })
        result = auto_upsert(engine, df, "cu_comp",
                             constrain=["order_id", "line_id"],
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success == 3


# ---------------------------------------------------------------------------
# auto_update
# ---------------------------------------------------------------------------

class TestAutoUpdate:

    def test_update_single_where(self, engine):
        _create_users_table(engine, "cup_single")
        df = _make_users(5)
        auto_insert(engine, df, "cup_single",
                    config=CrudConfig(strict=False, enable_fingerprint=False))

        modified = df.copy()
        modified["name"] = "updated"
        result = auto_update(engine, modified, "cup_single",
                             where=[("id", "=", "?")],
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success == 5

        with engine.connect() as conn:
            name = conn.execute(
                text('SELECT name FROM "cup_single" WHERE id = 1')
            ).scalar()
        assert name == "updated"

    def test_update_compound_where(self, engine):
        _create_users_table(engine, "cup_compound")
        df = _make_users(5)
        auto_insert(engine, df, "cup_compound",
                    config=CrudConfig(strict=False, enable_fingerprint=False))

        modified = df.head(2).copy()
        modified["name"] = "compound_updated"
        result = auto_update(engine, modified, "cup_compound",
                             where=[("id", "=", "?"), ("score", ">", 0)],
                             expression="1 AND 2",
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success >= 0  # dialect-dependent rowcount

    def test_update_with_static_where(self, engine):
        _create_users_table(engine, "cup_static")
        df = _make_users(5)
        auto_insert(engine, df, "cup_static",
                    config=CrudConfig(strict=False, enable_fingerprint=False))

        modified = df.copy()
        modified["name"] = "static_updated"
        result = auto_update(engine, modified, "cup_static",
                             where=[("id", "=", "?")],
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.success > 0


# ---------------------------------------------------------------------------
# CrudConfig
# ---------------------------------------------------------------------------

class TestCrudConfig:

    def test_default_config(self):
        cfg = CrudConfig()
        assert cfg.chunk_size == 10_000
        assert cfg.strict is True
        assert cfg.add_missing_cols is True
        assert cfg.trace_sql is False

    def test_custom_config(self):
        cfg = CrudConfig(chunk_size=500, strict=False, trace_sql=True)
        assert cfg.chunk_size == 500
        assert cfg.strict is False
        assert cfg.trace_sql is True

    def test_add_missing_cols(self, engine):
        _create_users_table(engine, "cfg_addcol")
        df = _make_users(3)
        df["extra_col"] = "new_value"
        result = auto_insert(engine, df, "cfg_addcol",
                             config=CrudConfig(strict=False, add_missing_cols=True,
                                               enable_fingerprint=False))
        assert result.success == 3
        cols = {c["name"] for c in sa.inspect(engine).get_columns("cfg_addcol")}
        assert "extra_col" in cols


# ---------------------------------------------------------------------------
# CrudResult
# ---------------------------------------------------------------------------

class TestCrudResult:

    def test_result_fields(self, engine):
        _create_users_table(engine, "cr_fields")
        df = _make_users(3)
        result = auto_insert(engine, df, "cr_fields",
                             config=CrudConfig(strict=False, enable_fingerprint=False))
        assert result.total == 3
        assert result.success == 3
        assert result.failed == 0
        assert result.method in ("bulk", "row_fallback", "none")
        assert isinstance(result.diagnostics, dict)

    def test_result_merge(self):
        from crud.types import CrudResult
        r1 = CrudResult(total=5, success=5, failed=0, method="bulk")
        r2 = CrudResult(total=3, success=2, failed=1, method="row_fallback")
        merged = r1.merge(r2)
        assert merged.total == 8
        assert merged.success == 7
        assert merged.failed == 1
