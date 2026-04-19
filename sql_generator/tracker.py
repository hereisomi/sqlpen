from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Union

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .common import ensure_connection, normalize_data, Row, Rows
from .router import update

try:
    import pandas as pd
except Exception:
    pd = None


def update_track(
    engine: Engine,
    data: Any,
    table: Union[str, sa.Table],
    where: List[Any],
    expression: str = "",
    schema: str | None = None,
    chunk: int = 1_000,
) -> Dict[str, int]:
    """
    Snapshot existing rows into {table}_tracker before updating them.

    1. Builds a SELECT using the WHERE key columns from data.
    2. Appends old rows + timestamp into {table}_tracker (auto-created if missing).
    3. Runs the UPDATE via router.update().

    Args:
        engine:     SQLAlchemy engine.
        data:       DataFrame / dict / list[dict] with new values.
        table:      Target table name.
        where:      WHERE conditions — same format as router.update().
        expression: Optional logical expression combining WHERE conditions.
        chunk:      Chunk size for tracker insert.

    Returns:
        {"tracked": int, "updated": int}
    """
    if pd is None:
        raise RuntimeError("pandas is required for update_track")

    table_name = table.name if isinstance(table, sa.Table) else table
    rows = normalize_data(data)
    if not rows:
        return {"tracked": 0, "updated": 0}

    # --- build SELECT to snapshot rows that will be updated ---
    key_cols = _extract_key_cols(where, rows[0])
    if not key_cols:
        raise ValueError("Could not extract key columns from where conditions")

    dialect = engine.dialect.name.lower()
    select_sql, params = _build_snapshot_select(table_name, key_cols, rows, dialect=dialect, schema=schema)

    with engine.connect() as conn:
        existing_df = pd.read_sql(sa.text(select_sql), conn, params=params)

    if existing_df.empty:
        return {"tracked": 0, "updated": 0}

    # --- write snapshot to tracker table ---
    tracker_name = f"{table_name}_tracker"
    existing_df["track_inserted_at"] = dt.datetime.now()

    insp = sa.inspect(engine)
    tracker_exists = insp.has_table(tracker_name, schema=schema)

    if not tracker_exists:
        # Use our robust DDL creator instead of pd.to_sql(replace) 
        # to avoid Oracle FLOAT precision errors.
        from utils.ddl_create import df_ddl
        ddl_str, _, existing_df = df_ddl(
            existing_df, tracker_name, 
            server=dialect, 
            schema=schema
        )
        with engine.begin() as conn:
            from sqlalchemy import text
            for stmt in ddl_str.split(";"):
                if stmt.strip():
                    conn.execute(text(stmt.strip()))
    
    # Now we can safely append
    existing_df.to_sql(
        tracker_name,
        engine,
        schema=schema,
        if_exists="append",
        index=False,
        chunksize=chunk,
    )

    # --- run the actual update ---
    updated = update(engine, table_name, data, where, expression=expression)

    return {"tracked": int(existing_df.shape[0]), "updated": updated}


def _extract_key_cols(where: List[Any], sample_row: Row) -> List[str]:
    """Extract column names from WHERE conditions, matching row casing."""
    cols = []
    row_keys_lower = {str(k).lower(): k for k in sample_row.keys()}
    
    for w in where:
        found_col = None
        if isinstance(w, tuple) and len(w) == 3:
            found_col = str(w[0])
        elif isinstance(w, str):
            parts = w.strip().split()
            if parts:
                found_col = parts[0]
        
        if found_col:
            # Match against row keys case-insensitively, but store the REAL key from data
            key_in_row = row_keys_lower.get(found_col.lower())
            if key_in_row:
                cols.append(key_in_row)
            else:
                # If not in row, we might still want to try to select it from DB
                # but for update_track we need the key in data to filter the SELECT.
                cols.append(found_col) 
    return list(dict.fromkeys(cols)) # uniq


def _build_snapshot_select(table_name: str, key_cols: List[str], rows: Rows, dialect: str = 'sqlite', schema: str | None = None) -> tuple:
    """Build SELECT ... WHERE key IN (...) to fetch rows about to be updated."""
    from .where_build import escape_identifier
    
    params: Dict[str, Any] = {}
    conds = []

    for col in key_cols:
        # Match values case-insensitively from the rows
        row_key = col # Default
        vals = list({r[col] for r in rows if col in r})
        
        if not vals:
            # Fallback for casing mismatch
            col_l = col.lower()
            for r in rows:
                for k in r.keys():
                    if k.lower() == col_l:
                        row_key = k
                        break
                if row_key != col: break
            vals = list({r[row_key] for r in rows if row_key in r})

        if not vals:
            continue
        
        col_esc = escape_identifier(col, dialect)
        if len(vals) == 1:
            params[col] = vals[0]
            conds.append(f"{col_esc} = :{col}")
        else:
            in_keys = [f"{col}_{i}" for i in range(len(vals))]
            params.update({k: v for k, v in zip(in_keys, vals)})
            conds.append(f"{col_esc} IN ({', '.join(':' + k for k in in_keys)})")

    if not conds:
        raise ValueError(f"No usable key values found in data for columns: {key_cols}")

    tbl_esc = escape_identifier(table_name, dialect)
    if schema:
        sch_esc = escape_identifier(schema, dialect)
        tbl_esc = f"{sch_esc}.{tbl_esc}"

    sql = f"SELECT * FROM {tbl_esc} WHERE {' AND '.join(conds)}"
    return sql, params
