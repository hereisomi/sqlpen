from __future__ import annotations

from typing import Any, Dict, List, Union

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine

from .common import (
    Row, Rows, chunk_rows, ensure_connection, exec_with_row_isolation,
    get_table, normalize_data, validate_columns_exist, write_sql_trace,
)


def _upsert_stmt(table: sa.Table, sample: Row) -> sa.sql.dml.Insert:
    ins = mysql.insert(table)
    return ins.on_duplicate_key_update(**{c: ins.inserted[c] for c in sample.keys()})


def upsert(engine: Engine, data: Any, table: Union[str, sa.Table], constrain: List[str], chunk: int = 10_000, tolerance: int = 5, trace_sql: bool = False) -> Dict[str, Any]:
    rows = normalize_data(data)
    if not rows:
        return {"total": 0, "success": 0, "failed": 0, "method": "none"}
    _ = constrain  # MySQL relies on table's PK/UNIQUE; constrain is informational only
    stats: Dict[str, Any] = {"total": len(rows), "success": 0, "failed": 0, "chunks": []}
    with ensure_connection(engine) as conn:
        tbl = get_table(conn, table)
        validate_columns_exist(rows, tbl)
        for part in chunk_rows(rows, chunk):
            def bulk_exec(rs: Rows) -> None:
                stmt = _upsert_stmt(tbl, rs[0])
                if trace_sql:
                    write_sql_trace("mysql_upsert", tbl.name, stmt, engine)
                conn.execute(stmt, rs)

            def row_exec(r: Row) -> None:
                conn.execute(_upsert_stmt(tbl, r), [r])

            chunk_stats = exec_with_row_isolation(part, bulk_exec, row_exec, tolerance)
            stats["chunks"].append(chunk_stats)
            stats["success"] += int(chunk_stats["success"])
            stats["failed"] += int(chunk_stats["failed"])
    return stats


def insert(engine: Engine, data: Any, table: Union[str, sa.Table], chunk_size: int = 10_000, tolerance: int = 5, trace_sql: bool = False) -> int:
    rows = normalize_data(data)
    if not rows:
        return 0
    total = 0
    with ensure_connection(engine) as conn:
        tbl = get_table(conn, table)
        validate_columns_exist(rows, tbl)
        stmt = tbl.insert()
        if trace_sql:
            write_sql_trace("mysql_insert", tbl.name, stmt, engine)
        for part in chunk_rows(rows, chunk_size):
            def bulk_exec(rs: Rows) -> None:
                conn.execute(stmt, rs)

            def row_exec(r: Row) -> None:
                conn.execute(stmt, [r])

            chunk_stats = exec_with_row_isolation(part, bulk_exec, row_exec, tolerance)
            total += int(chunk_stats["success"])
    return total
