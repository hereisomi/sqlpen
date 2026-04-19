from __future__ import annotations

"""Unified external caller for the sqlsql package.

This module wraps the common entry points:
- aligner.align: analyze/coerce (with optional DDL) without writing data
- pipeline.run: full analyze/coerce + insert/upsert/update/update_track
- df_tosql.df_tosql: convenience wrapper for file/df ingestion + pipeline

Usage examples:
    from pen import create_engine, align, pipeline_run, df_tosql
    eng = create_engine("sqlite:///test.db")
    result = pipeline_run(eng, df, table="my_table", schema=None, mode="insert", apply_ddl=True, dry_run=False)
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from aligner import (
    AlignmentPolicies,
    DEFAULT_POLICIES,
    align,
    reflect_table_spec,
    analyze,
    coerce_dataframe,
    apply_ddl_plan,
)
from pipeline import run as pipeline_run
from df_tosql import df_tosql
from utils.ddl_create import df_ddl
from sql_generator import insert as sg_insert, upsert as sg_upsert, update as sg_update, update_track as sg_update_track

__all__ = [
    "create_engine",
    "align",
    "reflect_table_spec",
    "analyze",
    "coerce_dataframe",
    "apply_ddl_plan",
    "AlignmentPolicies",
    "DEFAULT_POLICIES",
    "df_ddl",
    "sg_insert",
    "sg_upsert",
    "sg_update",
    "sg_update_track",
    "pipeline_run",
    "df_tosql",
    "run_pipeline",
    "explore_schema",
    "execute_sql",
]


def run_pipeline(
    engine: Engine,
    df: Any,
    table: str,
    schema: Optional[str] = None,
    *,
    mode: str = "insert",
    constrain: Optional[List[str]] = None,
    where: Optional[List[Any]] = None,
    expression: str = "",
    policies: Optional[AlignmentPolicies] = None,
    apply_ddl: bool = False,
    dry_run: bool = True,
    chunk: int = 10_000,
    tolerance: int = 5,
    trace_sql: bool = False,
) -> Dict[str, Any]:
    """Thin wrapper over pipeline.run for external callers.

    Parameters mirror pipeline.run; see that function for full semantics.
    """
    return pipeline_run(
        engine=engine,
        df=df,
        table=table,
        schema=schema,
        mode=mode,
        constrain=constrain,
        where=where,
        expression=expression,
        policies=policies or DEFAULT_POLICIES,
        apply_ddl=apply_ddl,
        dry_run=dry_run,
        chunk=chunk,
        tolerance=tolerance,
        trace_sql=trace_sql,
    )


def explore_schema(engine: Engine, schema: Optional[str] = None) -> Dict[str, Any]:
    """Inspect database tables/columns for quick validation/testing.

    Returns a dict {table_name: {"schema": schema, "columns": [(name, type)]}}.
    """
    from sqlalchemy import inspect

    insp = inspect(engine)
    tables = insp.get_table_names(schema=schema)
    out: Dict[str, Any] = {}
    for tbl in tables:
        cols = insp.get_columns(tbl, schema=schema)
        out[tbl] = {
            "schema": schema,
            "columns": [(c.get("name"), c.get("type")) for c in cols],
        }
    return out


def execute_sql(engine: Engine, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Execute raw SQL and return the result proxy/rows.

    Intended for quick manual testing; caller can iterate over returned rows.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        res = conn.execute(text(sql), params or {})
        try:
            return res.fetchall()
        except Exception:
            # For statements that do not return rows
            return res.rowcount
