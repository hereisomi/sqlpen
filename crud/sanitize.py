"""
Identifier escaping and column name sanitization.

Provides per-dialect quoting (Oracle double-quotes, MySQL backticks,
MSSQL brackets) and a reserved-word-aware column cleaner.
"""
from __future__ import annotations

import re
from keyword import iskeyword
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Reserved words per dialect
# ---------------------------------------------------------------------------
RESERVED_WORDS: Dict[str, set] = {
    "oracle": {
        "SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "GROUP", "ORDER",
        "BY", "CREATE", "TABLE", "INDEX", "VIEW", "PRIMARY", "KEY", "FOREIGN",
        "CONSTRAINT", "NUMBER", "DATE", "UNION", "ALL", "DISTINCT", "JOIN",
        "INNER", "OUTER", "LEFT", "RIGHT", "ON", "USING", "HAVING", "LIMIT",
        "OFFSET", "ALTER", "DROP", "TRUNCATE", "USER", "LEVEL", "ACCESS", "MODE",
    },
    "postgresql": {
        "SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "GROUP", "ORDER",
        "BY", "CREATE", "TABLE", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT",
        "REFERENCES", "BIGINT", "INTEGER", "SMALLINT", "VARCHAR", "TIMESTAMP",
        "BOOLEAN", "NUMERIC", "DECIMAL", "REAL",
    },
    "mysql": {
        "SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "TABLE",
        "CREATE", "DROP", "ALTER", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT",
        "INT", "VARCHAR", "DATETIME", "BIGINT", "SMALLINT", "AUTO_INCREMENT",
        "UNIQUE", "INDEX",
    },
    "mssql": {
        "SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "TABLE",
        "CREATE", "DROP", "ALTER", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT",
        "IDENTITY", "INT", "VARCHAR", "DATETIME", "BIT", "FLOAT", "NUMERIC",
        "DECIMAL", "BIGINT", "SMALLINT",
    },
    "sqlite": {
        "SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "TABLE",
        "CREATE", "DROP", "ALTER", "CONSTRAINT", "PRIMARY", "KEY", "FOREIGN",
        "UNIQUE", "CHECK", "DEFAULT",
    },
}

# Common SQL keywords always treated as reserved
_COMMON_RESERVED = {
    "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "LIMIT", "JOIN", "TABLE",
    "COLUMN", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
    "SCHEMA", "INDEX", "VIEW", "TRIGGER", "PROCEDURE", "FUNCTION",
    "DATABASE", "USER", "ROLE", "GRANT", "REVOKE",
}


# ---------------------------------------------------------------------------
# Identifier escaping
# ---------------------------------------------------------------------------
def escape_identifier(name: str, dialect: str = "oracle") -> str:
    """Escape a SQL identifier (table/column name) per dialect rules.

    - Oracle/PostgreSQL/SQLite: double-quote when needed
    - MySQL: backtick-quote when needed
    - MSSQL: always bracket-quote
    """
    d_key = _norm_dialect(dialect)
    reserved = RESERVED_WORDS.get(d_key, RESERVED_WORDS["oracle"])
    name_upper = name.upper()

    if d_key in ("oracle", "postgresql", "sqlite"):
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", name)
            and name_upper not in reserved
        ):
            return name.upper() if d_key == "oracle" else name
        return '"{}"'.format(name.replace('"', '""'))

    if d_key == "mysql":
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name)
            and name_upper not in reserved
        ):
            return name
        return "`{}`".format(name.replace("`", "``"))

    if d_key == "mssql":
        return f"[{name}]"

    return name


# ---------------------------------------------------------------------------
# Column name sanitization
# ---------------------------------------------------------------------------
def sanitize_columns(
    obj: Any,
    *,
    allow_space: bool = False,
    to_lower: bool = True,
    fallback: str = "col_",
    dialect: str = "postgresql",
) -> Any:
    """Clean column names / dict keys / DataFrame headers.

    Handles reserved words, special characters, leading digits, duplicates.
    """
    d_key = _norm_dialect(dialect)
    sql_kw = {kw.upper() for kw in RESERVED_WORDS.get(d_key, set())}
    sql_kw.update(_COMMON_RESERVED)
    blank_count = iter(range(1, 1_000_000))

    def _clean(s: Any) -> str:
        if not isinstance(s, str):
            s = str(s)
        s = s.strip()
        s = re.sub(r"[^\w\s]" if allow_space else r"[^\w]", "_", s)
        if allow_space:
            s = re.sub(r"\s+", " ", s)
        if to_lower:
            s = s.lower()
        s = re.sub(r"__+", "_", s).strip("_")
        if re.match(r"^\d", s):
            s = "_" + s
        if not s:
            s = f"{fallback}{next(blank_count)}"
        if s.upper() in sql_kw or iskeyword(s):
            s += "_"
        return s

    # --- dispatch by type ---
    if isinstance(obj, (list, tuple)) or (pd is not None and isinstance(obj, pd.Index)):
        seen: Dict[str, int] = {}
        clean: List[str] = []
        for n in obj:
            c = _clean(n)
            i = seen.get(c, 0)
            seen[c] = i + 1
            clean.append(c if i == 0 else f"{c}_{i}")
        return clean

    if isinstance(obj, dict):
        keys = sanitize_columns(list(obj.keys()), allow_space=allow_space, to_lower=to_lower, fallback=fallback, dialect=d_key)
        return dict(zip(keys, obj.values()))

    if pd is not None and isinstance(obj, pd.DataFrame):
        df = obj.copy()
        df.columns = sanitize_columns(df.columns.tolist(), allow_space=allow_space, to_lower=to_lower, fallback=fallback, dialect=d_key)
        return df

    if isinstance(obj, str):
        return _clean(obj)

    return obj


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _norm_dialect(dialect: str) -> str:
    d = dialect.lower()
    if d in ("postgres", "postgresql"):
        return "postgresql"
    if d == "mariadb":
        return "mysql"
    return d
