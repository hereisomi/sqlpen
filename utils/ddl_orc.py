"""Oracle-specific DDL generator.

This isolates the Oracle quirks from the generic `ddl_create.py` logic so we can
iterate without risking other dialects.  Public helper:
    build_create_table(df: DataFrame, table: str, pk: Iterable[str] | None = None) -> str
Returns a single-line CREATE TABLE statement ready for execution.
"""
from __future__ import annotations

import re
from typing import Iterable, List

import pandas as pd

__all__ = ["build_create_table"]

# Oracle reserved words (partial, extended when needed)
_ORC_RESERVED = {
    "SELECT","INSERT","DELETE","UPDATE","WHERE","FROM","GROUP","ORDER","COUNT","BY","CREATE","TABLE",
    "INDEX","VIEW","PRIMARY","KEY","FOREIGN","CONSTRAINT","NUMBER","DATE","UNION","ALL","DISTINCT",
    "JOIN","INNER","OUTER","LEFT","RIGHT","ON","USING","HAVING","LIMIT","OFFSET","ALTER","DROP",
    "TRUNCATE","TO","DESC","ASC","VALUE","EXISTS","CASE","WHEN","THEN","ELSE","END",
}

_COLUMN_RE = re.compile(r"^[A-Z][A-Z0-9_$#]*$")

_SQL_TYPE_MAP = {
    "int64": "NUMBER",
    "float64": "BINARY_DOUBLE",
    "object": "VARCHAR2(255)",
    "bool": "NUMBER(1,0)",
    "datetime64[ns]": "TIMESTAMP",
}

def _sanitize(name: str) -> str:
    """Return a valid Oracle identifier (unquoted), uppercase."""
    name = str(name).strip().replace(" ", "_").replace("__", "_")
    if not name:
        name = "COL"
    if name[0].isdigit():
        name = f"N{name}"
    name = re.sub(r"[^A-Za-z0-9_$#]", "_", name)
    name_up = name.upper()
    if name_up in _ORC_RESERVED:
        name = f"{name}_"  # append underscore
        name_up = name.upper()
    # truncate to 30 chars
    if len(name_up) > 30:
        name_up = name_up[:30]
    return name_up

def _sql_type(dtype: str) -> str:
    return _SQL_TYPE_MAP.get(dtype, "VARCHAR2(255)")

def build_create_table(df: pd.DataFrame | str, table: str, pk: Iterable[str] | None = None) -> str:
    """Return single-line CREATE TABLE statement for Oracle."""
    if isinstance(df, str):
        df = pd.read_csv(df, nrows=0)  # header only
    cols: List[str] = []
    pk_set = {c.upper() for c in pk or []}
    for col in df.columns:
        # Assume df.columns are already sanitized (uppercase, no spaces)
        col_name = col
        dtype = str(df[col].dtype).lower()
        sql_type = _sql_type(dtype)
        nullable = "NOT NULL"  # for simplicity; Oracle allows NULL by default
        cols.append(f"{col_name} {sql_type} {nullable}")
    cols_joined = ", ".join(cols)
    table_name = _sanitize(table)
    ddl = f"CREATE TABLE {table_name} ({cols_joined})"
    return ddl
