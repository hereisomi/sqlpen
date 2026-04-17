"""
Quick sanity tests for peek.py introspection module.
Run with: pytest peek_test.py -v
"""
import pytest
import sqlalchemy as sa
import pandas as pd

import peek as pk


@pytest.fixture
def memory_engine():
    """Returns a fresh SQLite in-memory engine."""
    return sa.create_engine("sqlite:///:memory:")


def test_get_engine(memory_engine):
    """Test engine resolution works."""
    # With explicit URL
    eng = pk.get_engine("sqlite:///:memory:")
    assert eng is not None
    
    # Test engine is usable
    with eng.connect() as conn:
        result = conn.execute(sa.text("SELECT 1"))
        assert result.scalar() == 1


def test_tables_empty(memory_engine):
    """Test tables() on empty database."""
    tables = pk.tables(url=str(memory_engine.url))
    assert tables == []


def test_tables_with_data(memory_engine):
    """Test tables() after creating a table."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.commit()
    
    tables = pk.tables(url=str(memory_engine.url))
    assert "users" in tables


def test_describe(memory_engine):
    """Test describe() returns column info."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL)"))
        conn.commit()
    
    df = pk.describe("users", url=str(memory_engine.url))
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    
    # Check expected columns in result
    assert "name" in df.columns
    assert "type" in df.columns
    assert "nullable" in df.columns
    
    # Check column names are present
    names = df["name"].tolist()
    assert "id" in names
    assert "email" in names


def test_query(memory_engine):
    """Test query() executes SQL and returns DataFrame."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER, name TEXT)"))
        conn.execute(sa.text("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')"))
        conn.commit()
    
    df = pk.query("SELECT * FROM users", url=str(memory_engine.url))
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["id", "name"]


def test_query_with_params(memory_engine):
    """Test query() with parameter binding."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE users (id INTEGER, name TEXT)"))
        conn.execute(sa.text("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')"))
        conn.commit()
    
    df = pk.query(
        "SELECT * FROM users WHERE id = :user_id",
        url=str(memory_engine.url),
        params={"user_id": 1}
    )
    assert len(df) == 1
    assert df.iloc[0]["name"] == "Alice"


def test_has_table(memory_engine):
    """Test has_table() detection."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE test_table (id INTEGER)"))
        conn.commit()
    
    assert pk.has_table("test_table", url=str(memory_engine.url)) is True
    assert pk.has_table("nonexistent", url=str(memory_engine.url)) is False


def test_get_pk(memory_engine):
    """Test primary key retrieval."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text("CREATE TABLE pk_test (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.commit()
    
    pks = pk.get_pk("pk_test", url=str(memory_engine.url))
    assert "id" in pks


def test_query_clean(memory_engine):
    """Test query_clean() with column sanitization."""
    with memory_engine.connect() as conn:
        conn.execute(sa.text('CREATE TABLE clean_test ("User ID" INTEGER, "Email Address" TEXT)'))
        conn.execute(sa.text("INSERT INTO clean_test VALUES (1, 'test@example.com')"))
        conn.commit()
    
    df = pk.query_clean(
        'SELECT * FROM clean_test',
        url=str(memory_engine.url)
    )
    # After cleaning, column names should be sanitized
    assert "user_id" in df.columns or "User ID" in df.columns


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
