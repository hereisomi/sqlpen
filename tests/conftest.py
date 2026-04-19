"""
Shared fixtures for all test modules.
Engine is created once from --url CLI arg and reused across tests.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine


# ── pytest configuration ────────────────────────────────────────────────────────

def pytest_addoption(parser):
    """Register custom command-line options for pytest."""
    parser.addoption(
        "--url", 
        action="store", 
        default="sqlite:///:memory:", 
        help="SQLAlchemy engine URL for tests"
    )

# ── CLI arg parsing (Fallback for python script execution) ────────────────────

def parse_url(argv=None) -> str:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--url", default="sqlite:///:memory:", help="SQLAlchemy engine URL")
    args, _ = p.parse_known_args(argv or sys.argv[1:])
    return args.url


def make_engine(url: Optional[str] = None) -> Engine:
    url = url or parse_url()
    return sa.create_engine(url)

# ── pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def engine(request):
    """Shared engine using the URL provided by the --url pytest option."""
    url = request.config.getoption("--url")
    eng = sa.create_engine(url)
    drop_employees(eng) # Force fresh drop to handle type/casing changes
    create_employees(eng)
    seed_employees(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def tmp_engine(request):
    """Bare engine (no pre-created tables) using URL provided by --url."""
    url = request.config.getoption("--url")
    eng = sa.create_engine(url)
    yield eng
    eng.dispose()



# ── Shared table setup helpers ────────────────────────────────────────────────

def get_employees_table(meta: sa.MetaData) -> sa.Table:
    return sa.Table(
        "employees", meta,
        sa.Column("emp_id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("dept", sa.String(50)),
        sa.Column("salary", sa.Numeric(15, 2))
    )

def create_employees(engine: Engine) -> None:
    meta = sa.MetaData()
    get_employees_table(meta)
    meta.create_all(engine)


def drop_employees(engine: Engine) -> None:
    meta = sa.MetaData()
    get_employees_table(meta)
    sa.Table("employees_tracker", meta,
             sa.Column("emp_id", sa.Integer),
             sa.Column("name", sa.String(50)),
             sa.Column("dept", sa.String(50)),
             sa.Column("salary", sa.Numeric(15, 2)),
             sa.Column("_updated_at", sa.DateTime)
    )
    meta.drop_all(engine)


def seed_employees(engine: Engine) -> None:
    meta = sa.MetaData()
    emp = get_employees_table(meta)
    with engine.begin() as conn:
        conn.execute(emp.delete())
        conn.execute(emp.insert(), [
            {"emp_id": 1, "name": "Alice", "dept": "Engineering", "salary": 90000},
            {"emp_id": 2, "name": "Bob", "dept": "Marketing", "salary": 70000},
            {"emp_id": 3, "name": "Carol", "dept": "Engineering", "salary": 85000}
        ])


def fetch_all(engine: Engine, table: str) -> list:
    with engine.connect() as conn:
        result = conn.execute(sa.text(f"SELECT * FROM {table}"))
        return [dict(r._mapping) for r in result]
