"""
test_peek.py — Tests for peek.py introspection functions

Covers:
- tables() / show_tables()
- describe() — column DataFrame
- describe_full() — PKs, unique constraints, identity columns
- has_table() / table_exists()
- query() — returns DataFrame
- query_clean() — sanitized column names
- validate_upsert() — constraint check
- get_engine() — URL resolution

Run against any database:
    pytest tests/test_peek.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import text

import peek as pk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def peek_db(tmp_path_factory, request):
    """
    Module-scoped fixture that creates the peek_test table.

    For SQLite (default): uses a file-based DB so the URL and engine
    share the same connection. For other dialects: uses the session engine
    from conftest and returns its URL.
    """
    cli_url = request.config.getoption("--url")

    if cli_url == "sqlite:///:memory:" or cli_url is None:
        # File-based SQLite so URL and engine share the same DB
        db_file = tmp_path_factory.mktemp("peek") / "peek.db"
        url = f"sqlite:///{db_file}"
        engine = sa.create_engine(url)
    else:
        url = cli_url
        engine = sa.create_engine(url)

    dialect = engine.dialect.name.lower()

    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS "peek_test"'))

    if dialect == "sqlite":
        ddl = """
            CREATE TABLE "peek_test" (
                "id"    INTEGER NOT NULL PRIMARY KEY,
                "name"  VARCHAR(100) NOT NULL,
                "email" VARCHAR(255),
                "score" REAL
            )
        """
    elif dialect == "postgresql":
        ddl = """
            CREATE TABLE "peek_test" (
                "id"    BIGINT NOT NULL PRIMARY KEY,
                "name"  VARCHAR(100) NOT NULL,
                "email" VARCHAR(255) UNIQUE,
                "score" DOUBLE PRECISION
            )
        """
    elif dialect in ("mysql", "mariadb"):
        ddl = """
            CREATE TABLE `peek_test` (
                `id`    BIGINT NOT NULL PRIMARY KEY,
                `name`  VARCHAR(100) NOT NULL,
                `email` VARCHAR(255) UNIQUE,
                `score` DOUBLE
            )
        """
    elif dialect == "mssql":
        ddl = """
            CREATE TABLE [peek_test] (
                [id]    BIGINT NOT NULL PRIMARY KEY,
                [name]  VARCHAR(100) NOT NULL,
                [email] VARCHAR(255) UNIQUE,
                [score] FLOAT
            )
        """
    else:
        ddl = """
            CREATE TABLE "peek_test" (
                "id"    NUMBER(19) NOT NULL PRIMARY KEY,
                "name"  VARCHAR2(100) NOT NULL,
                "email" VARCHAR2(255) UNIQUE,
                "score" NUMBER
            )
        """

    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(
            'INSERT INTO "peek_test" VALUES (1, :n, :e, :s)'
        ), {"n": "Alice", "e": "alice@test.com", "s": 95.5})
        conn.execute(text(
            'INSERT INTO "peek_test" VALUES (2, :n, :e, :s)'
        ), {"n": "Bob", "e": "bob@test.com", "s": 80.0})

    yield url, engine

    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS "peek_test"'))
    engine.dispose()


@pytest.fixture
def purl(peek_db):
    """The URL that points to the peek test database."""
    return peek_db[0]


@pytest.fixture
def pdialect(peek_db):
    return peek_db[1].dialect.name.lower()


# ---------------------------------------------------------------------------
# tables()
# ---------------------------------------------------------------------------

class TestTables:

    def test_tables_returns_list(self, purl):
        result = pk.tables(url=purl)
        assert isinstance(result, list)

    def test_peek_test_in_tables(self, purl):
        result = pk.tables(url=purl)
        assert "peek_test" in result

    def test_show_tables_alias(self, purl):
        assert pk.show_tables(url=purl) == pk.tables(url=purl)


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------

class TestDescribe:

    def test_describe_returns_dataframe(self, purl):
        result = pk.describe("peek_test", url=purl)
        assert isinstance(result, pd.DataFrame)

    def test_describe_has_expected_columns(self, purl):
        result = pk.describe("peek_test", url=purl)
        assert "name" in result.columns
        assert "type" in result.columns
        assert "nullable" in result.columns

    def test_describe_correct_column_count(self, purl):
        result = pk.describe("peek_test", url=purl)
        assert len(result) == 4  # id, name, email, score

    def test_describe_nonexistent_table_raises(self, purl):
        with pytest.raises((ValueError, sa.exc.NoSuchTableError)):
            pk.describe("nonexistent_xyz_table", url=purl)


# ---------------------------------------------------------------------------
# describe_full()
# ---------------------------------------------------------------------------

class TestDescribeFull:

    def test_describe_full_returns_dict(self, purl):
        result = pk.describe_full("peek_test", url=purl)
        assert isinstance(result, dict)

    def test_describe_full_has_required_keys(self, purl):
        result = pk.describe_full("peek_test", url=purl)
        assert "table" in result
        assert "columns" in result
        assert "primary_keys" in result
        assert "unique_constraints" in result
        assert "identity_columns" in result

    def test_describe_full_table_name(self, purl):
        result = pk.describe_full("peek_test", url=purl)
        assert result["table"] == "peek_test"

    def test_describe_full_primary_key(self, purl):
        result = pk.describe_full("peek_test", url=purl)
        assert "id" in result["primary_keys"]


# ---------------------------------------------------------------------------
# has_table() / table_exists()
# ---------------------------------------------------------------------------

class TestHasTable:

    def test_existing_table_returns_true(self, purl):
        assert pk.has_table("peek_test", url=purl) is True

    def test_missing_table_returns_false(self, purl):
        assert pk.has_table("definitely_not_a_table_xyz", url=purl) is False

    def test_table_exists_alias(self, purl):
        assert pk.table_exists("peek_test", url=purl) is True


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------

class TestQuery:

    def test_query_returns_dataframe(self, purl):
        result = pk.query("SELECT * FROM peek_test", url=purl)
        assert isinstance(result, pd.DataFrame)

    def test_query_correct_row_count(self, purl):
        result = pk.query("SELECT * FROM peek_test", url=purl)
        assert len(result) == 2

    def test_query_with_params(self, purl):
        result = pk.query(
            "SELECT * FROM peek_test WHERE id = :id",
            url=purl, params={"id": 1}
        )
        assert len(result) == 1
        assert result.iloc[0]["name"] == "Alice"

    def test_query_aggregate(self, purl):
        result = pk.query("SELECT COUNT(*) as cnt FROM peek_test", url=purl)
        assert result.iloc[0]["cnt"] == 2

    def test_query_empty_result(self, purl):
        result = pk.query(
            "SELECT * FROM peek_test WHERE id = :id",
            url=purl, params={"id": 9999}
        )
        assert len(result) == 0
        assert isinstance(result, pd.DataFrame)


# ---------------------------------------------------------------------------
# query_clean()
# ---------------------------------------------------------------------------

class TestQueryClean:

    def test_query_clean_returns_dataframe(self, purl):
        result = pk.query_clean("SELECT * FROM peek_test", url=purl)
        assert isinstance(result, pd.DataFrame)

    def test_query_clean_lowercases_columns(self, purl):
        result = pk.query_clean("SELECT * FROM peek_test", url=purl)
        for col in result.columns:
            assert col == col.lower()


# ---------------------------------------------------------------------------
# validate_upsert()
# ---------------------------------------------------------------------------

class TestValidateUpsert:

    def test_valid_pk_constraint_passes(self, purl):
        # id is a PK — should not raise
        pk.validate_upsert("peek_test", key_cols=["id"], url=purl)

    def test_invalid_constraint_raises(self, purl):
        with pytest.raises(Exception):
            pk.validate_upsert("peek_test", key_cols=["score"], url=purl)


# ---------------------------------------------------------------------------
# get_engine()
# ---------------------------------------------------------------------------

class TestGetEngine:

    def test_get_engine_from_url(self, purl):
        engine = pk.get_engine(url=purl)
        assert engine is not None
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()
