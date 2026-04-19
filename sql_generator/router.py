from __future__ import annotations

from typing import Any, Dict, List, Union

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from . import mysql, oracle, postgres, sqlite

from . import mssql
from .common import ensure_connection, get_table, normalize_data, validate_columns_exist, write_sql_trace
from .where_build import build_update


def _dialect_name(engine: Engine) -> str:
    name = engine.dialect.name.lower()
    if "mssql" in name or "sqlserver" in name:
        return "mssql"
    if name in ("postgres", "postgresql"):
        return "postgresql"
    return name


def upsert(engine: Engine, data: Any, table: Union[str, sa.Table], constrain: List[str], chunk: int = 10_000, tolerance: int = 5, trace_sql: bool = False) -> Dict[str, Any]:
    d = _dialect_name(engine)
    if d == "postgresql":
        return postgres.upsert(engine, data, table, constrain, chunk, tolerance, trace_sql)
    if d == "sqlite":
        return sqlite.upsert(engine, data, table, constrain, chunk, tolerance, trace_sql)
    if d == "mysql":
        return mysql.upsert(engine, data, table, constrain, chunk, tolerance, trace_sql)
    if d == "oracle":
        return oracle.upsert(engine, data, table, constrain, chunk, tolerance, trace_sql)
    if d == "mssql":
        return mssql.upsert(engine, data, table, constrain, chunk, tolerance, trace_sql)
    raise ValueError(f"Unsupported database dialect: {d}")


def insert(engine: Engine, data: Any, table: Union[str, sa.Table], chunk_size: int = 10_000, tolerance: int = 5, trace_sql: bool = False) -> int:
    d = _dialect_name(engine)
    if d == "postgresql":
        return postgres.insert(engine, data, table, chunk_size, tolerance, trace_sql)
    if d == "sqlite":
        return sqlite.insert(engine, data, table, chunk_size, tolerance, trace_sql)
    if d == "mysql":
        return mysql.insert(engine, data, table, chunk_size, tolerance, trace_sql)
    if d == "oracle":
        return oracle.insert(engine, data, table, chunk_size, tolerance, trace_sql)
    if d == "mssql":
        return mssql.insert(engine, data, table, chunk_size, tolerance, trace_sql)
    raise ValueError(f"Unsupported database dialect: {d}")


def update(engine: Engine, table: Union[str, sa.Table], data: Any, where: List[Any], expression: str = "", trace_sql: bool = False) -> int:
    table_name = table.name if isinstance(table, sa.Table) else table
    rows = normalize_data(data)
    if not rows:
        return 0
    dialect = _dialect_name(engine)
    total = 0
    with ensure_connection(engine) as conn:
        tbl = get_table(conn, table_name)
        validate_columns_exist(rows, tbl)
        for i, r in enumerate(rows):
            sql, params = build_update(r, table_name, where, dialect=dialect, expression=expression or None)
            if trace_sql:
                write_sql_trace("router_update", f"{table_name}_row{i}", sql, engine)
            safe_params = {k: v.item() if hasattr(v, 'item') else v for k, v in params.items()}
            res = conn.execute(sa.text(sql), safe_params)
            total += int(res.rowcount or 0)
    return total
