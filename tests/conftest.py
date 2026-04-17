"""
conftest.py — Shared fixtures for the SqlPen test suite.

Provides a CLI-injectable SQLAlchemy engine fixture so every test module
can be run against any supported database dialect.

Usage:
    # Default: SQLite in-memory
    pytest tests/

    # Against PostgreSQL
    pytest tests/ --url "postgresql://user:pass@localhost/mydb"

    # Against MySQL
    pytest tests/ --url "mysql+pymysql://user:pass@localhost/mydb"

    # Against SQL Server
    pytest tests/ --url "mssql+pyodbc://user:pass@host/db?driver=ODBC+Driver+17+for+SQL+Server"

    # Against Oracle
    pytest tests/ --url "oracle+cx_oracle://user:pass@host:1521/SID"

    # Run a single module against a specific DB
    pytest tests/test_df_tosql.py --url "postgresql://user:pass@localhost/mydb" -v
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa


# ---------------------------------------------------------------------------
# CLI option
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--url",
        action="store",
        default="sqlite:///:memory:",
        help="SQLAlchemy connection URL. Defaults to sqlite:///:memory:",
    )


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def db_url(request) -> str:
    return request.config.getoption("--url")


@pytest.fixture(scope="session")
def engine(db_url):
    """
    Session-scoped SQLAlchemy engine resolved from --url CLI option.
    Defaults to SQLite in-memory if --url is not provided.
    """
    eng = sa.create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def dialect(engine) -> str:
    """Lowercase dialect name: sqlite | postgresql | mysql | mssql | oracle."""
    return engine.dialect.name.lower()


# ---------------------------------------------------------------------------
# Sample DataFrame factories
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_df():
    """10-row mixed-type DataFrame with clean column names."""
    import pandas as pd
    import numpy as np
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "id":     range(1, 11),
        "name":   [f"User_{i}" for i in range(1, 11)],
        "email":  [f"user{i}@test.com" for i in range(1, 11)],
        "score":  rng.random(10) * 100,
        "active": rng.choice([True, False], size=10),
    })


@pytest.fixture
def dirty_df():
    """10-row DataFrame with dirty column names, string types, and an outlier."""
    import pandas as pd
    return pd.DataFrame({
        " ID ":    [str(i) for i in range(1, 11)],
        "Email!":  [f"user{i}@test.com" for i in range(1, 11)],
        " Score ": [float(i * 10) if i < 10 else 9999.0 for i in range(1, 11)],
        "Active":  ["true", "false"] * 5,
        "dt":      [f"2024-01-{i:02d}" for i in range(1, 11)],
    })


@pytest.fixture
def large_df():
    """1000-row DataFrame for bulk/chunk testing."""
    import pandas as pd
    import numpy as np
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "id":    range(1, 1001),
        "name":  [f"User_{i}" for i in range(1, 1001)],
        "value": rng.random(1000) * 1000,
        "flag":  rng.choice([True, False], size=1000),
    })
