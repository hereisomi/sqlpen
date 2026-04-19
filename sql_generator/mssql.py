from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .common import (
    Row, Rows, chunk_rows, ensure_connection, exec_with_row_isolation,
    get_table, normalize_data, validate_columns_exist, validate_constrain_unique,
    write_sql_trace,
)


from .where_build import escape_identifier

def _merge_sql(table_name: str, key_cols: Tuple[str, ...], sample: Row) -> str:
    """Build MSSQL MERGE statement with proper identifier quoting."""
    cols = list(sample.keys())

    q_table = escape_identifier(table_name, "mssql")
    q_cols = [escape_identifier(c, "mssql") for c in cols]
    q_key_cols = [escape_identifier(c, "mssql") for c in key_cols]

    key_lower = {k.lower() for k in key_cols}
    update_cols = [c for c in cols if c.lower() not in key_lower]
    q_update_cols = [escape_identifier(c, "mssql") for c in update_cols]

    src_cols = ", ".join(f":{c} AS {q}" for c, q in zip(cols, q_cols))
    on_sql = " AND ".join(f"tgt.{q} = src.{q}" for q in q_key_cols)

    ins_cols = ", ".join(q_cols)
    ins_vals = ", ".join(f"src.{q}" for q in q_cols)

    sql = f"MERGE INTO {q_table} AS tgt USING (SELECT {src_cols}) AS src ON ({on_sql})"
    if q_update_cols:
        sql += " WHEN MATCHED THEN UPDATE SET " + ", ".join(
            f"tgt.{q} = src.{q}" for q in q_update_cols
        )
    sql += f" WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals});"
    return sql


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
            sql = _merge_sql(tbl.name, key_cols, part[0])
            if trace_sql:
                write_sql_trace("mssql_upsert", tbl.name, sql, engine)

            def bulk_exec(rs: Rows) -> None:
                for r in rs:
                    conn.execute(sa.text(sql), r)

            def row_exec(r: Row) -> None:
                conn.execute(sa.text(sql), r)

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
            write_sql_trace("mssql_insert", tbl.name, stmt, engine)
        for part in chunk_rows(rows, chunk_size):
            def bulk_exec(rs: Rows) -> None:
                conn.execute(stmt, rs)

            def row_exec(r: Row) -> None:
                conn.execute(stmt, [r])

            chunk_stats = exec_with_row_isolation(part, bulk_exec, row_exec, tolerance)
            total += int(chunk_stats["success"])
    return total
