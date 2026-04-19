"""
SQLAlchemy-backed DataFrame ➜ CREATE TABLE generator.

Replaces legacy `misc.ddl_create` string builder.
Only dependencies: `pandas` + `sqlalchemy`. Dialect-specific
syntax is delegated to SQLAlchemy compiler so we don't hand-craft SQL.

Returned tuple mirrors the legacy signature `(ddl_sql, meta)` so upstream
callers remain unchanged.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.schema import CreateTable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper – dtype mapping
# ---------------------------------------------------------------------------

_PANDAS2SA: Dict[str, sa.types.TypeEngine] = {
    "int64": sa.BigInteger(),
    "int32": sa.Integer(),
    "Int64": sa.BigInteger(),
    "float64": sa.Float(),
    "float32": sa.Float(),
    "boolean": sa.Boolean(),
    "bool": sa.Boolean(),
    "object": sa.String(255),
    "string": sa.String(255),
    "category": sa.String(255),
}

# Datetime types
for _dt in (
    "datetime64[ns]",
    "datetime64[us]",
    "datetime",
    "date",
):
    _PANDAS2SA[_dt] = sa.DateTime()

# ---------------------------------------------------------------------------
# Dialect factory helper
# ---------------------------------------------------------------------------

_DIALECT_FACTORY = {
    "postgresql": lambda: sa.dialects.postgresql.dialect(),
    "postgres": lambda: sa.dialects.postgresql.dialect(),
    "mysql": lambda: sa.dialects.mysql.dialect(),
    "mariadb": lambda: sa.dialects.mysql.dialect(),
    "sqlite": lambda: sa.dialects.sqlite.dialect(),
    "mssql": lambda: sa.dialects.mssql.dialect(),
    "oracle": lambda: sa.dialects.oracle.dialect(),
}


def _get_dialect(name: str) -> sa.engine.Dialect:
    key = name.lower()
    factory = _DIALECT_FACTORY.get(key)
    if not factory:
        logger.warning("Unknown dialect '%s'. Using default SQL dialect.", name)
        return sa.dialects.default.DefaultDialect()
    try:
        return factory()
    except (ImportError, AttributeError) as exc:  # pragma: no cover
        logger.warning("Failed to initialise dialect '%s': %s. Using default.", key, exc)
        return sa.dialects.default.DefaultDialect()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def df_ddl(
    df: pd.DataFrame,
    table: str,
    server: str = "postgresql",
    schema: Optional[str] = None,
    pk: Optional[Sequence[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Return CREATE TABLE statement and JSON meta for *df*.

    Parameters
    ----------
    df : pd.DataFrame
    table : str
    server : str
        SQL dialect key (postgresql, mysql, mssql, oracle, sqlite).
    schema : str, optional
    pk : iterable[str], optional
        Primary-key column(s).
    """
    if df.empty:
        raise ValueError("Input DataFrame is empty")
    if not table:
        raise ValueError("Table name must be non-empty")

    metadata = MetaData(schema=schema)

    columns: List[Column] = []
    for col in df.columns:
        pd_type = str(df[col].dtype)
        sa_type = _PANDAS2SA.get(pd_type, sa.String(255))
        is_nullable = bool(df[col].isna().any())
        col_kwargs = {"nullable": is_nullable}
        columns.append(Column(col, sa_type, **col_kwargs))

    pk_cols = list(pk) if pk else []
    tbl = Table(table, metadata, *columns, sa.PrimaryKeyConstraint(*pk_cols) if pk_cols else None)

    # Compile statement
    dialect = _get_dialect(server)
    ddl_sql = str(CreateTable(tbl).compile(dialect=dialect)).strip()

    # meta JSON similar to legacy output
    meta = {
        "server": server,
        "schema": schema,
        "table": table,
        "columns": [
            {
                "name": c.name,
                "pandas_dtype": str(df[c.name].dtype),
                "sql_dtype": str(c.type),
                "nullable": c.nullable,
            }
            for c in tbl.columns
        ],
        "primary_key": pk_cols,
        "row_count": len(df),
        "column_count": len(df.columns),
    }

    return ddl_sql + ";", meta, df
