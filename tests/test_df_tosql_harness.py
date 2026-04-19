"""
Harness-based integration test for df_tosql.
Run: python tests/test_df_tosql_harness.py --url sqlite:///:memory:
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import sqlalchemy as sa

from df_tosql import df_tosql
from utils.harness import CrudTestHarness
from tests.conftest import make_engine, parse_url


def _make_source_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": 1, "name": "Alice", "score": 10.5, "active": True, "created_at": "2024-01-01"},
            {"id": 2, "name": "Bob", "score": 12.0, "active": False, "created_at": "2024-01-02"},
            {"id": 3, "name": "Carol", "score": 9.0, "active": True, "created_at": "2024-01-03"},
            {"id": 4, "name": "Dave", "score": 7.5, "active": True, "created_at": "2024-01-04"},
            {"id": 5, "name": "Eve", "score": 15.2, "active": False, "created_at": "2024-01-05"},
            {"id": 6, "name": "Frank", "score": 11.1, "active": True, "created_at": "2024-01-06"},
            {"id": 7, "name": "Grace", "score": 8.9, "active": True, "created_at": "2024-01-07"},
            {"id": 8, "name": "Hank", "score": 14.2, "active": False, "created_at": "2024-01-08"},
        ]
    )


def _fetch_df(engine: sa.engine.Engine, sql: str, params: dict) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(sa.text(sql), conn, params=params)


def run_all(url: str) -> tuple[int, int]:
    engine = make_engine(url)

    src_df = _make_source_df()
    harness = CrudTestHarness(src_df, pk_cols="id", constraint_cols="id", table_name="df_tosql_harness")

    # INSERT
    df_tosql(
        df=harness.insert_df,
        table=harness.table,
        engine=engine,
        if_exist="insert",
        table_constraints={"pk": ["id"]},
        add_new_column=True,
    )
    spec = harness.get_insert_check_query()
    db_df = _fetch_df(engine, spec.sql, spec.params)
    harness.validate_after_insert(db_df)

    # UPSERT
    df_tosql(
        df=harness.upsert_df,
        table=harness.table,
        engine=engine,
        if_exist="upsert",
        table_constraints={"pk": ["id"]},
        add_new_column=True,
    )
    spec = harness.get_upsert_check_query()
    db_df = _fetch_df(engine, spec.sql, spec.params)
    harness.validate_after_upsert(db_df)

    # UPDATE
    df_tosql(
        df=harness.update_df,
        table=harness.table,
        engine=engine,
        if_exist="update",
        table_constraints={"pk": ["id"]},
        where=[("id", "=", "?")],
        add_new_column=True,
    )
    spec = harness.get_update_check_query()
    db_df = _fetch_df(engine, spec.sql, spec.params)
    harness.validate_after_full_cycle(db_df)

    return 3, 0


if __name__ == "__main__":
    url = parse_url()
    print(f"\n=== test_df_tosql_harness  [{url}] ===")
    passed, failed = run_all(url)
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
