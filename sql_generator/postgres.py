from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from .common import (
    Row, Rows, chunk_rows, ensure_connection, exec_with_row_isolation,
    get_table, normalize_data, validate_columns_exist, validate_constrain_unique,
    write_sql_trace,
)


def _upsert_stmt(table: sa.Table, key_cols: Tuple[str, ...], sample: Row) -> sa.sql.dml.Insert:
    ins = postgresql.insert(table)
    update_cols = {c: ins.excluded[c] for c in sample.keys() if c not in key_cols}
    return ins.on_conflict_do_update(index_elements=list(key_cols), set_=update_cols)


def upsert(engine: Engine, data: Any, table: Union[str, sa.Table], constrain: List[str], chunk: int = 10_000, tolerance: int = 5, trace_sql: bool = False) -> Dict[str, Any]:
    rows = normalize_data(data)
    if not rows:
        return {"total": 0, "success": 0, "failed": 0, "method": "none"}
    stats: Dict[str, Any] = {"total": len(rows), "success": 0, "failed": 0, "chunks": []}
    with ensure_connection(engine) as conn:
        tbl = get_table(conn, table)
        validate_columns_exist(rows, tbl)
        key_cols = validate_constrain_unique(conn, tbl, constrain)
        for part in chunk_rows(rows, chunk):
            def bulk_exec(rs: Rows) -> None:
                stmt = _upsert_stmt(tbl, key_cols, rs[0])
                if trace_sql:
                    write_sql_trace("postgres_upsert", tbl.name, stmt, engine)
                conn.execute(stmt, rs)

            def row_exec(r: Row) -> None:
                conn.execute(_upsert_stmt(tbl, key_cols, r), [r])

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
            write_sql_trace("postgres_insert", tbl.name, stmt, engine)
        for part in chunk_rows(rows, chunk_size):
            def bulk_exec(rs: Rows) -> None:
                conn.execute(stmt, rs)

            def row_exec(r: Row) -> None:
                conn.execute(stmt, [r])

            chunk_stats = exec_with_row_isolation(part, bulk_exec, row_exec, tolerance)
            total += int(chunk_stats["success"])
    return total
