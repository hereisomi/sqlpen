"""
Standalone DataFrame -> DDL + schema JSON generator.

Features kept from the original module (engine-free):
- DDL generation (CREATE TABLE) for oracle, postgresql, mysql, mssql, sqlite
- JSON schema metadata generation
- Column name sanitization with mapping + constraint remapping
- Server-aware identifier escaping + reserved words validation
- dtype -> SQL type mapping with lightweight object semantic inference
- PK, FK, UNIQUE constraints
- Autoincrement column (server-aware inline clauses)
- Optional partition clause generation ONLY for Oracle and MSSQL

Partition notes (engine-free reality):
- Oracle partitioning is supported via concrete DDL text:
  - PARTITION BY RANGE/LIST/HASH (<expr>)
  - Optional INTERVAL for RANGE
  - Optional explicit partition definitions for RANGE and LIST
- MSSQL does not have inline CREATE TABLE partition syntax like Oracle.
  - You need a partition function + partition scheme and then place the table/index on it.
  - This module supports generating a companion MSSQL script (function+scheme) and
    appending "ON <scheme>(<column>)" to CREATE TABLE.
  - You must provide boundary values for RANGE RIGHT/LEFT.

No SQLAlchemy, no external utilities, no engine required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from keyword import iskeyword
from typing import Any, Dict, List, Mapping, Sequence, Tuple, Union

import pandas as pd


# ----------------------------
# Configuration
# ----------------------------

ORACLE_MAX_IDENTIFIER = 30
DEFAULT_VARCHAR_SIZE = 255

SUPPORTED_SERVERS = ("oracle", "postgresql", "mysql", "mssql", "sqlite")

RESERVED_WORDS: Dict[str, set[str]] = {
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

DTYPE_MAP: Dict[str, Dict[str, str]] = {
    "oracle": {
        "object": "VARCHAR2(255)", "string": "VARCHAR2(255)", "category": "VARCHAR2(255)",
        "Int64": "NUMBER", "int64": "NUMBER",
        "Float64": "BINARY_DOUBLE", "float64": "BINARY_DOUBLE", "float32": "BINARY_FLOAT",
        "boolean": "NUMBER(1,0)", "bool": "NUMBER(1,0)",
        "datetime64[ns]": "TIMESTAMP", "datetime64[us]": "TIMESTAMP",
        "timedelta64[ns]": "INTERVAL DAY TO SECOND", "timedelta64[us]": "INTERVAL DAY TO SECOND",
        "date": "DATE", "time": "DATE", "datetime": "TIMESTAMP", "timedelta": "INTERVAL DAY TO SECOND",
    },
    "sqlite": {
        "object": "TEXT", "string": "TEXT", "category": "TEXT",
        "Int64": "INTEGER", "int64": "INTEGER",
        "Float64": "REAL", "float64": "REAL", "float32": "REAL",
        "boolean": "INTEGER", "bool": "INTEGER",
        "datetime64[ns]": "DATETIME", "datetime64[us]": "DATETIME",
        "timedelta64[ns]": "TEXT", "timedelta64[us]": "TEXT",
        "date": "DATE", "time": "TIME", "datetime": "DATETIME", "timedelta": "TEXT",
    },
    "mssql": {
        "object": "VARCHAR(255)", "string": "VARCHAR(255)", "category": "VARCHAR(255)",
        "Int64": "BIGINT", "int64": "BIGINT",
        "Float64": "FLOAT", "float64": "FLOAT", "float32": "REAL",
        "boolean": "BIT", "bool": "BIT",
        "datetime64[ns]": "DATETIME2", "datetime64[us]": "DATETIME2",
        "timedelta64[ns]": "VARCHAR(255)", "timedelta64[us]": "VARCHAR(255)",
        "date": "DATE", "time": "TIME", "datetime": "DATETIME2", "timedelta": "VARCHAR(255)",
    },
    "mysql": {
        "object": "VARCHAR(255)", "string": "VARCHAR(255)", "category": "VARCHAR(255)",
        "Int64": "BIGINT", "int64": "BIGINT",
        "Float64": "DOUBLE", "float64": "DOUBLE", "float32": "FLOAT",
        "boolean": "TINYINT", "bool": "TINYINT",
        "datetime64[ns]": "DATETIME", "datetime64[us]": "DATETIME",
        "timedelta64[ns]": "VARCHAR(255)", "timedelta64[us]": "VARCHAR(255)",
        "date": "DATE", "time": "TIME", "datetime": "DATETIME", "timedelta": "VARCHAR(255)",
    },
    "postgresql": {
        "object": "TEXT", "string": "TEXT", "category": "TEXT",
        "Int64": "BIGINT", "int64": "BIGINT",
        "Float64": "DOUBLE PRECISION", "float64": "DOUBLE PRECISION", "float32": "REAL",
        "boolean": "BOOLEAN", "bool": "BOOLEAN",
        "datetime64[ns]": "TIMESTAMP", "datetime64[us]": "TIMESTAMP",
        "timedelta64[ns]": "INTERVAL", "timedelta64[us]": "INTERVAL",
        "date": "DATE", "time": "TIME", "datetime": "TIMESTAMP", "timedelta": "INTERVAL",
    },
}


# ----------------------------
# Types
# ----------------------------

@dataclass(frozen=True)
class AutoIncrement:
    column: str
    initial_value: int


@dataclass(frozen=True)
class OraclePartitionSpec:
    kind: str
    expr: List[str]
    interval: Union[str, None] = None
    partitions: Union[List[Dict[str, str]], None] = None


@dataclass(frozen=True)
class MsSqlPartitionSpec:
    column: str
    boundary_values: List[str]
    range_right: bool = True
    partition_function: Union[str, None] = None
    partition_scheme: Union[str, None] = None
    filegroups: Union[List[str], None] = None
    data_space: Union[str, None] = None


ForeignKey = Tuple[str, str, str]
UniqueGroup = Union[str, List[str]]


# ----------------------------
# Small utilities
# ----------------------------

def server_key(server: str) -> str:
    s = (server or "").strip().lower()
    if s == "postgres":
        s = "postgresql"
    if s not in SUPPORTED_SERVERS:
        raise ValueError(f"Unsupported server: {server}")
    return s


def normalize_cols(cols: Union[str, Sequence[str], None]) -> List[str]:
    if cols is None:
        out: List[str] = []
        return out
    if isinstance(cols, str):
        out = [cols]
        return out
    out = list(cols)
    return out


def ensure_nonempty_df(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    if df.empty:
        raise ValueError("DataFrame is empty")


def ensure_table_name(table: str) -> None:
    if not isinstance(table, str):
        raise TypeError("table must be a string")
    if not table.strip():
        raise ValueError("table must be a non-empty string")


def truncate_identifier(name: str, max_len: int) -> str:
    if len(name) <= max_len:
        out = name
        return out
    out = name[:max_len]
    return out


def is_valid_unquoted_identifier(name: str, s_key: str) -> bool:
    if s_key == "oracle":
        pattern = r"[A-Za-z_][A-Za-z0-9_$#]*"
    else:
        pattern = r"[A-Za-z_][A-Za-z0-9_$]*"
    ok = re.fullmatch(pattern, name) is not None
    return ok


def escape_identifier(name: str, server: str) -> str:
    s_key = server_key(server)
    reserved = RESERVED_WORDS.get(s_key, set())
    upper = name.upper()

    if s_key in ("oracle", "postgresql", "sqlite"):
        if is_valid_unquoted_identifier(name, s_key) and upper not in reserved:
            out = name.upper() if s_key == "oracle" else name
            return out
        out = '"' + name.replace('"', '""') + '"'
        return out

    if s_key == "mysql":
        if is_valid_unquoted_identifier(name, s_key) and upper not in reserved:
            out = name
            return out
        out = "`" + name.replace("`", "``") + "`"
        return out

    if s_key == "mssql":
        out = "[" + name + "]"
        return out

    raise ValueError(f"Unsupported server: {server}")


def qualify_table(table: str, schema_name: Union[str, None], server: str) -> str:
    t = escape_identifier(table, server)
    if not schema_name:
        out = t
        return out
    s = escape_identifier(schema_name, server)
    out = f"{s}.{t}"
    return out


def sanitize_name(name: Any, allow_space: bool, to_lower: bool, fallback: str, used: Dict[str, int], server: str, idx_seed: int) -> Tuple[str, int]:
    if not isinstance(name, str):
        name = str(name)

    s = name.strip()
    s = re.sub(r"[^\w\s]" if allow_space else r"[^\w]", "_", s)

    if allow_space:
        s = re.sub(r"\s+", " ", s)

    if to_lower:
        s = s.lower()

    s = re.sub(r"__+", "_", s).strip("_")

    if re.match(r"^\d", s):
        s = "_" + s

    if not s:
        idx_seed += 1
        s = f"{fallback}{idx_seed}"

    if s.upper() in RESERVED_WORDS.get(server_key(server), set()) or iskeyword(s):
        s = s + "_"

    n = used.get(s, 0)
    used[s] = n + 1

    if n == 0:
        out = s
        return out, idx_seed

    out = f"{s}_{n}"
    return out, idx_seed


def sanitize_dataframe_columns(df: pd.DataFrame, server: str, allow_space: bool = False, to_lower: bool = True, fallback: str = "col_") -> Tuple[pd.DataFrame, Dict[str, str]]:
    used: Dict[str, int] = {}
    idx_seed = 0
    orig = list(df.columns)
    new_cols: List[str] = []

    for c in orig:
        cleaned, idx_seed = sanitize_name(c, allow_space, to_lower, fallback, used, server, idx_seed)
        new_cols.append(cleaned)

    out_df = df.copy()
    out_df.columns = new_cols
    mapping = dict(zip([str(x) for x in orig], new_cols))
    return out_df, mapping


def normalize_dtype(dtype: Any) -> str:
    d = str(dtype).lower()
    if d in ("int64", "int32", "int16", "int8"):
        out = "Int64"
        return out
    if d in ("float64", "float32"):
        out = "Float64"
        return out
    if d == "bool":
        out = "boolean"
        return out
    out = d
    return out


def infer_object_semantic(series: pd.Series, sample_size: int = 50) -> str:
    non_null = series.dropna()
    if non_null.empty:
        out = "STRING_OBJECT"
        return out

    take = non_null.head(sample_size)
    has_structured = False
    has_non_str = False

    for v in take.tolist():
        if isinstance(v, (dict, list, tuple, set)):
            has_structured = True
        elif not isinstance(v, str):
            has_non_str = True

    if has_structured:
        out = "TRUE_OBJECT"
        return out
    if has_non_str:
        out = "STRUCTURED_OBJECT"
        return out

    out = "STRING_OBJECT"
    return out


def object_sql_type(server: str, semantic: Union[str, None], varchar_sizes: Union[Dict[str, int], None], col_name: Union[str, None]) -> Union[str, None]:
    s_key = server_key(server)
    if semantic is None:
        out: Union[str, None] = None
        return out

    size = DEFAULT_VARCHAR_SIZE
    if varchar_sizes is not None and col_name is not None:
        size = int(varchar_sizes.get(col_name, DEFAULT_VARCHAR_SIZE))

    if semantic == "STRING_OBJECT":
        if s_key == "oracle":
            out = f"VARCHAR2({size})"
            return out
        if s_key == "sqlite":
            out = "TEXT"
            return out
        if s_key in ("mysql", "mssql"):
            out = f"VARCHAR({size})"
            return out
        if s_key == "postgresql":
            out = "TEXT"
            return out

    if semantic in ("STRUCTURED_OBJECT", "TRUE_OBJECT"):
        if s_key in ("oracle", "sqlite"):
            out = "BLOB"
            return out
        if s_key == "mssql":
            out = "VARBINARY(MAX)"
            return out
        if s_key == "mysql":
            out = "JSON"
            return out
        if s_key == "postgresql":
            out = "JSONB"
            return out

    out = None
    return out


def sql_type_for_series(series: pd.Series, server: str, varchar_sizes: Union[Dict[str, int], None], semantic_override: Union[str, None]) -> Tuple[str, str]:
    s_key = server_key(server)
    dtype_map = DTYPE_MAP[s_key]
    raw_dtype = str(series.dtype)
    norm = normalize_dtype(series.dtype)

    semantic = semantic_override
    if semantic is None and norm == "object":
        semantic = infer_object_semantic(series)

    if norm == "object" and semantic is not None:
        obj_type = object_sql_type(s_key, semantic, varchar_sizes, str(series.name))
        if obj_type is not None:
            return obj_type, semantic

    direct = dtype_map.get(norm)
    if direct is not None:
        return direct, semantic or ""

    cd = raw_dtype.lower()
    if any(x in cd for x in ("datetime", "date", "time")):
        fallback = dtype_map.get("datetime", "VARCHAR(255)")
        return fallback, semantic or ""

    if "timedelta" in cd:
        fallback = dtype_map.get("timedelta", "VARCHAR(255)")
        return fallback, semantic or ""

    size = DEFAULT_VARCHAR_SIZE
    if varchar_sizes is not None:
        size = int(varchar_sizes.get(str(series.name), DEFAULT_VARCHAR_SIZE))

    fallback = "TEXT" if s_key in ("postgresql", "sqlite") else f"VARCHAR({size})"
    return fallback, semantic or ""


def column_nullable_from_data(series: pd.Series) -> bool:
    out = bool(series.isna().any())
    return out


def sample_value(series: pd.Series) -> Union[str, None]:
    non_null = series.dropna()
    if non_null.empty:
        out: Union[str, None] = None
        return out
    out = str(non_null.iloc[0])
    return out


# ----------------------------
# Partition support (Oracle + MSSQL only)
# ----------------------------

def _partition_expr_sql(expr: List[str], server: str) -> str:
    out: List[str] = []
    s_key = server_key(server)

    for e in expr:
        e = (e or "").strip()
        if not e:
            raise ValueError("Empty partition expression element")
        if is_valid_unquoted_identifier(e, s_key):
            out.append(escape_identifier(e, s_key))
        else:
            out.append(e)

    sql = ", ".join(out)
    return sql


def validate_oracle_partition(partition: OraclePartitionSpec) -> None:
    kind = (partition.kind or "").strip().lower()
    if kind not in ("range", "list", "hash"):
        raise ValueError(f"Unsupported Oracle partition kind: {partition.kind}")

    if not partition.expr:
        raise ValueError("OraclePartitionSpec.expr must be non-empty")

    if partition.partitions is not None and not isinstance(partition.partitions, list):
        raise ValueError("OraclePartitionSpec.partitions must be a list of dicts or None")

    if partition.interval is not None and kind != "range":
        raise ValueError("OraclePartitionSpec.interval is only valid for RANGE partitioning")


def oracle_partition_clause(partition: OraclePartitionSpec) -> str:
    validate_oracle_partition(partition)

    kind = partition.kind.strip().lower()
    expr_sql = _partition_expr_sql(partition.expr, "oracle")
    clause = f"PARTITION BY {kind.upper()} ({expr_sql})"

    if partition.interval is not None:
        clause = clause + f" INTERVAL ({partition.interval})"

    if partition.partitions is None:
        return clause

    parts = partition.partitions
    if not parts:
        return clause

    blocks: List[str] = []
    if kind == "range":
        for p in parts:
            pname = (p.get("name", "") or "").strip()
            pvals = (p.get("values", "") or "").strip()
            if not pname:
                raise ValueError("Oracle RANGE partition entry missing 'name'")
            if not pvals:
                raise ValueError("Oracle RANGE partition entry missing 'values'")
            blocks.append(f"PARTITION {escape_identifier(pname, 'oracle')} VALUES LESS THAN ({pvals})")
    elif kind == "list":
        for p in parts:
            pname = (p.get("name", "") or "").strip()
            pvals = (p.get("values", "") or "").strip()
            if not pname:
                raise ValueError("Oracle LIST partition entry missing 'name'")
            if not pvals:
                raise ValueError("Oracle LIST partition entry missing 'values'")
            blocks.append(f"PARTITION {escape_identifier(pname, 'oracle')} VALUES ({pvals})")
    else:
        raise ValueError("Explicit partition definitions are supported for Oracle RANGE and LIST only")

    clause = clause + " (\n    " + ",\n    ".join(blocks) + "\n)"
    return clause


def validate_mssql_partition(partition: MsSqlPartitionSpec) -> None:
    if not partition.column or not partition.column.strip():
        raise ValueError("MsSqlPartitionSpec.column must be a non-empty string")

    if not partition.boundary_values:
        raise ValueError("MsSqlPartitionSpec.boundary_values must be non-empty")

    for v in partition.boundary_values:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("MsSqlPartitionSpec.boundary_values must be non-empty strings")

    if partition.filegroups is not None:
        if not partition.filegroups:
            raise ValueError("MsSqlPartitionSpec.filegroups cannot be an empty list")

    if partition.data_space is not None and not partition.data_space.strip():
        raise ValueError("MsSqlPartitionSpec.data_space must be non-empty if provided")


def default_mssql_partition_names(table: str) -> Tuple[str, str]:
    pf = f"pf_{table}"
    ps = f"ps_{table}"
    return pf, ps


def mssql_partition_script(table: str, partition: MsSqlPartitionSpec) -> Tuple[str, str, str]:
    """
    Returns (pre_script, on_clause, meta_name).

    pre_script: CREATE PARTITION FUNCTION/SCHEME statements
    on_clause: appended to CREATE TABLE: " ON <scheme>(<column>)" or " ON [PRIMARY]"
    meta_name: scheme name used
    """
    validate_mssql_partition(partition)

    pf_default, ps_default = default_mssql_partition_names(table)
    pf = partition.partition_function or pf_default
    ps = partition.partition_scheme or ps_default
    pf_esc = escape_identifier(pf, "mssql")
    ps_esc = escape_identifier(ps, "mssql")

    range_dir = "RIGHT"
    if not partition.range_right:
        range_dir = "LEFT"

    boundaries = ", ".join(partition.boundary_values)
    data_space = partition.data_space or "PRIMARY"
    data_space_esc = escape_identifier(data_space, "mssql")

    filegroups = partition.filegroups
    if filegroups is None:
        filegroups = [data_space] * (len(partition.boundary_values) + 1)

    if len(filegroups) != len(partition.boundary_values) + 1:
        raise ValueError("MsSqlPartitionSpec.filegroups must be len(boundary_values) + 1")

    fg_list = ", ".join(escape_identifier(fg, "mssql") for fg in filegroups)

    pre_script_lines: List[str] = []
    pre_script_lines.append(f"CREATE PARTITION FUNCTION {pf_esc} (BIGINT) AS RANGE {range_dir} FOR VALUES ({boundaries})")
    pre_script_lines.append(f"CREATE PARTITION SCHEME {ps_esc} AS PARTITION {pf_esc} TO ({fg_list})")

    pre_script = "\n".join(pre_script_lines)
    on_clause = f" ON {ps_esc}({escape_identifier(partition.column, 'mssql')})"
    return pre_script, on_clause, ps


# ----------------------------
# Constraints and DDL parts
# ----------------------------

def build_pk_constraint(table: str, pk_cols: List[str], server: str) -> str:
    s_key = server_key(server)
    pk_list = ", ".join(escape_identifier(c, s_key) for c in pk_cols)

    if s_key == "sqlite":
        out = f"PRIMARY KEY ({pk_list})"
        return out

    name_max = ORACLE_MAX_IDENTIFIER if s_key == "oracle" else 128
    name = truncate_identifier(f"{table}_PK", name_max)
    cname = escape_identifier(name, s_key)
    out = f"CONSTRAINT {cname} PRIMARY KEY ({pk_list})"
    return out


def build_fk_constraint(table: str, col: str, ref_tab: str, ref_col: str, idx: int, server: str) -> str:
    s_key = server_key(server)
    col_esc = escape_identifier(col, s_key)
    ref_tab_esc = escape_identifier(ref_tab, s_key)
    ref_col_esc = escape_identifier(ref_col, s_key)

    if s_key == "sqlite":
        out = f"FOREIGN KEY ({col_esc}) REFERENCES {ref_tab_esc}({ref_col_esc})"
        return out

    name_max = ORACLE_MAX_IDENTIFIER if s_key == "oracle" else 128
    name = truncate_identifier(f"{table}_fk{idx}", name_max)
    cname = escape_identifier(name, s_key)
    out = f"CONSTRAINT {cname} FOREIGN KEY ({col_esc}) REFERENCES {ref_tab_esc}({ref_col_esc})"
    return out


def build_unique_constraint(table: str, cols: List[str], idx: int, server: str) -> str:
    s_key = server_key(server)
    col_list = ", ".join(escape_identifier(c, s_key) for c in cols)

    if s_key == "sqlite":
        out = f"UNIQUE ({col_list})"
        return out

    name_max = ORACLE_MAX_IDENTIFIER if s_key == "oracle" else 128
    name = truncate_identifier(f"{table}_uq{idx}", name_max)
    cname = escape_identifier(name, s_key)
    out = f"CONSTRAINT {cname} UNIQUE ({col_list})"
    return out


def autoincrement_inline(column: str, server: str, initial_value: int) -> str:
    s_key = server_key(server)
    col_esc = escape_identifier(column, s_key)

    if s_key == "sqlite":
        out = f"{col_esc} INTEGER PRIMARY KEY AUTOINCREMENT"
        return out
    if s_key == "mysql":
        out = f"{col_esc} BIGINT AUTO_INCREMENT"
        return out
    if s_key == "mssql":
        out = f"{col_esc} BIGINT IDENTITY({initial_value},1)"
        return out
    if s_key == "postgresql":
        out = f"{col_esc} SERIAL"
        return out
    if s_key == "oracle":
        out = f"{col_esc} NUMBER GENERATED ALWAYS AS IDENTITY (START WITH {initial_value} INCREMENT BY 1)"
        return out

    raise ValueError(f"Unsupported server: {server}")


def should_inline_autopk(pk_cols: List[str], auto: Union[AutoIncrement, None], server: str) -> bool:
    if auto is None:
        out = False
        return out
    if len(pk_cols) != 1:
        out = False
        return out
    if pk_cols[0] != auto.column:
        out = False
        return out
    out = server_key(server) in ("sqlite", "mysql", "mssql", "postgresql", "oracle")
    return out


# ----------------------------
# Validation (engine-free)
# ----------------------------

def validate_constraints(df: pd.DataFrame, pk: Union[str, List[str], None], fk: Union[List[ForeignKey], None], unique: Union[List[UniqueGroup], None], autoincrement: Union[AutoIncrement, None]) -> None:
    cols = set(df.columns.tolist())

    if autoincrement is not None:
        if autoincrement.column not in cols:
            raise ValueError(f"Autoincrement column '{autoincrement.column}' not in DataFrame")
        if not pd.api.types.is_integer_dtype(df[autoincrement.column].dtype):
            raise ValueError("Autoincrement column must be integer type")
        if autoincrement.initial_value < 1:
            raise ValueError("Autoincrement initial_value must be >= 1")

    pk_cols = normalize_cols(pk)
    if pk_cols:
        if len(set(pk_cols)) != len(pk_cols):
            raise ValueError(f"Duplicate column in primary key: {pk_cols}")
        for c in pk_cols:
            if c not in cols:
                raise ValueError(f"Primary key references non-existent column '{c}'")

    if fk is not None:
        for i, (c, rt, rc) in enumerate(fk, 1):
            if c not in cols:
                raise ValueError(f"Foreign key {i} references non-existent column '{c}'")
            if not isinstance(rt, str) or not rt.strip():
                raise ValueError(f"Foreign key {i} references invalid table")
            if not isinstance(rc, str) or not rc.strip():
                raise ValueError(f"Foreign key {i} references invalid column")

    if unique is not None:
        for i, group in enumerate(unique, 1):
            ucols = normalize_cols(group)
            if not ucols:
                raise ValueError(f"Unique constraint {i} is empty")
            if len(set(ucols)) != len(ucols):
                raise ValueError(f"Duplicate column in unique constraint {i}: {ucols}")
            for c in ucols:
                if c not in cols:
                    raise ValueError(f"Unique constraint {i} references non-existent column '{c}'")


def validate_identifiers(df: pd.DataFrame, server: str, table: str, schema_name: Union[str, None], allow_unsafe: bool) -> None:
    s_key = server_key(server)
    reserved = RESERVED_WORDS[s_key]
    problems: List[str] = []

    def _bad(name: str) -> bool:
        if name.upper() in reserved:
            out = True
            return out
        if not is_valid_unquoted_identifier(name, s_key):
            out = True
            return out
        out = False
        return out

    if _bad(table):
        problems.append(f"table:{table}")

    if schema_name and _bad(schema_name):
        problems.append(f"schema:{schema_name}")

    for c in df.columns.astype(str).tolist():
        if _bad(c):
            problems.append(f"col:{c}")

    if problems and not allow_unsafe:
        raise ValueError(f"Invalid or reserved identifiers for {s_key}: {problems}")


# ----------------------------
# Core builders
# ----------------------------

def build_create_table_ddl(df: pd.DataFrame, table: str, server: str, schema_name: Union[str, None], pk: Union[str, List[str], None], fk: Union[List[ForeignKey], None], unique: Union[List[UniqueGroup], None], autoincrement: Union[AutoIncrement, None], varchar_sizes: Union[Dict[str, int], None], dtype_semantics: Union[Dict[str, str], None], infer_nullable: bool, oracle_partition: Union[OraclePartitionSpec, None], mssql_partition: Union[MsSqlPartitionSpec, None]) -> Tuple[str, Union[str, None]]:
    s_key = server_key(server)
    escaped_cols = {str(c): escape_identifier(str(c), s_key) for c in df.columns}
    col_lines: List[str] = []

    pk_cols = normalize_cols(pk)
    dtype_semantics = dtype_semantics or {}

    for col in df.columns.astype(str).tolist():
        series = df[col]
        semantic = dtype_semantics.get(col)
        sql_type, _semantic = sql_type_for_series(series, s_key, varchar_sizes, semantic)
        col_esc = escaped_cols[col]

        if autoincrement is not None and col == autoincrement.column:
            inline = autoincrement_inline(col, s_key, autoincrement.initial_value)
            col_lines.append(inline + " NOT NULL")
            continue

        nullable = "NULL"
        if infer_nullable and not column_nullable_from_data(series):
            nullable = "NOT NULL"

        col_lines.append(f"{col_esc} {sql_type} {nullable}")

    add_pk = bool(pk_cols)
    if add_pk and should_inline_autopk(pk_cols, autoincrement, s_key):
        add_pk = False
    if add_pk:
        col_lines.append(build_pk_constraint(table, pk_cols, s_key))

    if fk is not None:
        for i, (c, rt, rc) in enumerate(fk, 1):
            col_lines.append(build_fk_constraint(table, c, rt, rc, i, s_key))

    if unique is not None:
        for i, group in enumerate(unique, 1):
            col_lines.append(build_unique_constraint(table, normalize_cols(group), i, s_key))

    qname = qualify_table(table, schema_name if s_key != "sqlite" else None, s_key)
    ddl = "CREATE TABLE " + qname + " (\n    " + ",\n    ".join(col_lines) + "\n)"

    pre_script: Union[str, None] = None

    if oracle_partition is not None:
        if s_key != "oracle":
            raise ValueError("oracle_partition is only valid for server='oracle'")
        ddl = ddl + "\n" + oracle_partition_clause(oracle_partition)

    if mssql_partition is not None:
        if s_key != "mssql":
            raise ValueError("mssql_partition is only valid for server='mssql'")
        pre_script, on_clause, _scheme_name = mssql_partition_script(table, mssql_partition)
        ddl = ddl + on_clause

    return ddl, pre_script


def build_schema_json(df: pd.DataFrame, table: str, server: str, schema_name: Union[str, None], pk: Union[str, List[str], None], fk: Union[List[ForeignKey], None], unique: Union[List[UniqueGroup], None], autoincrement: Union[AutoIncrement, None], varchar_sizes: Union[Dict[str, int], None], dtype_semantics: Union[Dict[str, str], None], col_mapping: Union[Dict[str, str], None], infer_nullable: bool, oracle_partition: Union[OraclePartitionSpec, None], mssql_partition: Union[MsSqlPartitionSpec, None], pre_script: Union[str, None]) -> Dict[str, Any]:
    s_key = server_key(server)
    dtype_semantics = dtype_semantics or {}
    columns: List[Dict[str, Any]] = []

    for col in df.columns.astype(str).tolist():
        series = df[col]
        semantic = dtype_semantics.get(col)
        sql_type, inferred_semantic = sql_type_for_series(series, s_key, varchar_sizes, semantic)

        is_nullable = True
        if infer_nullable:
            is_nullable = column_nullable_from_data(series)

        entry: Dict[str, Any] = {
            "name": col,
            "pandas_dtype": str(series.dtype),
            "sql_dtype": sql_type,
            "nullable": bool(is_nullable),
            "sample_value": sample_value(series),
        }

        sem = semantic or inferred_semantic
        if sem:
            entry["semantic_type"] = sem

        columns.append(entry)

    oracle_part_meta: Union[Dict[str, Any], None] = None
    if oracle_partition is not None:
        oracle_part_meta = {
            "kind": oracle_partition.kind,
            "expr": list(oracle_partition.expr),
            "interval": oracle_partition.interval,
            "partitions": oracle_partition.partitions,
        }

    mssql_part_meta: Union[Dict[str, Any], None] = None
    if mssql_partition is not None:
        mssql_part_meta = {
            "column": mssql_partition.column,
            "boundary_values": list(mssql_partition.boundary_values),
            "range_right": bool(mssql_partition.range_right),
            "partition_function": mssql_partition.partition_function,
            "partition_scheme": mssql_partition.partition_scheme,
            "filegroups": list(mssql_partition.filegroups) if mssql_partition.filegroups is not None else None,
            "data_space": mssql_partition.data_space,
            "pre_script": pre_script,
        }

    meta: Dict[str, Any] = {
        "server": s_key,
        "schema": schema_name if s_key != "sqlite" else None,
        "table": table,
        "columns": columns,
        "primary_key": normalize_cols(pk),
        "foreign_keys": [{"column": c, "references_table": rt, "references_column": rc} for (c, rt, rc) in (fk or [])],
        "unique_constraints": [normalize_cols(g) for g in (unique or [])],
        "autoincrement": {"column": autoincrement.column, "initial_value": autoincrement.initial_value} if autoincrement else None,
        "oracle_partition": oracle_part_meta,
        "mssql_partition": mssql_part_meta,
        "column_mapping": col_mapping,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
    }
    return meta


# ----------------------------
# Public API
# ----------------------------

def remap_constraints(mapping: Mapping[str, str], pk: Union[str, List[str], None], fk: Union[List[ForeignKey], None], unique: Union[List[UniqueGroup], None], autoincrement: Union[AutoIncrement, None]) -> Tuple[Union[str, List[str], None], Union[List[ForeignKey], None], Union[List[UniqueGroup], None], Union[AutoIncrement, None]]:
    if not mapping:
        out = (pk, fk, unique, autoincrement)
        return out

    if isinstance(pk, str):
        pk = mapping.get(pk, pk)
    elif isinstance(pk, list):
        pk = [mapping.get(c, c) for c in pk]

    if fk is not None:
        fk = [(mapping.get(c, c), rt, rc) for (c, rt, rc) in fk]

    if unique is not None:
        new_unique: List[UniqueGroup] = []
        for g in unique:
            if isinstance(g, str):
                new_unique.append(mapping.get(g, g))
            else:
                new_unique.append([mapping.get(c, c) for c in g])
        unique = new_unique

    if autoincrement is not None:
        col = mapping.get(autoincrement.column, autoincrement.column)
        autoincrement = AutoIncrement(column=col, initial_value=autoincrement.initial_value)

    out = (pk, fk, unique, autoincrement)
    return out


def df_to_ddl_and_schema(df: pd.DataFrame, table: str, server: str, schema_name: Union[str, None] = None, pk: Union[str, List[str], None] = None, fk: Union[List[ForeignKey], None] = None, unique: Union[List[UniqueGroup], None] = None, autoincrement: Union[AutoIncrement, None] = None, varchar_sizes: Union[Dict[str, int], None] = None, dtype_semantics: Union[Dict[str, str], None] = None, sanitize: bool = False, allow_unsafe_names: bool = False, infer_nullable: bool = True, oracle_partition: Union[OraclePartitionSpec, None] = None, mssql_partition: Union[MsSqlPartitionSpec, None] = None) -> Tuple[pd.DataFrame, str, Dict[str, Any]]:
    ensure_nonempty_df(df)
    ensure_table_name(table)

    s_key = server_key(server)
    work_df = df.copy()
    mapping: Union[Dict[str, str], None] = None

    if sanitize:
        work_df, mapping = sanitize_dataframe_columns(work_df, s_key)
        pk, fk, unique, autoincrement = remap_constraints(mapping, pk, fk, unique, autoincrement)
        if mssql_partition is not None:
            mapped_col = mapping.get(mssql_partition.column, mssql_partition.column)
            mssql_partition = MsSqlPartitionSpec(
                column=mapped_col,
                boundary_values=mssql_partition.boundary_values,
                range_right=mssql_partition.range_right,
                partition_function=mssql_partition.partition_function,
                partition_scheme=mssql_partition.partition_scheme,
                filegroups=mssql_partition.filegroups,
                data_space=mssql_partition.data_space,
            )

    validate_identifiers(work_df, s_key, table, schema_name, allow_unsafe_names)
    validate_constraints(work_df, pk, fk, unique, autoincrement)

    if oracle_partition is not None and s_key != "oracle":
        raise ValueError("oracle_partition can only be used with server='oracle'")
    if mssql_partition is not None and s_key != "mssql":
        raise ValueError("mssql_partition can only be used with server='mssql'")

    if oracle_partition is not None:
        validate_oracle_partition(oracle_partition)

    pre_script: Union[str, None] = None
    if mssql_partition is not None:
        validate_mssql_partition(mssql_partition)
        if mssql_partition.column not in work_df.columns.astype(str).tolist():
            raise ValueError(f"MSSQL partition column '{mssql_partition.column}' not found in DataFrame columns")

    ddl, pre_script = build_create_table_ddl(
        work_df, table, s_key, schema_name, pk, fk, unique, autoincrement,
        varchar_sizes, dtype_semantics, infer_nullable, oracle_partition, mssql_partition,
    )

    meta = build_schema_json(
        work_df, table, s_key, schema_name, pk, fk, unique, autoincrement,
        varchar_sizes, dtype_semantics, mapping, infer_nullable,
        oracle_partition, mssql_partition, pre_script,
    )

    return work_df, ddl, meta


def schema_to_json_text(schema: Mapping[str, Any], indent: int = 2) -> str:
    txt = json.dumps(dict(schema), indent=indent, ensure_ascii=False, default=str)
    return txt


def read_csv_to_df(path: str, **read_csv_kwargs: Any) -> pd.DataFrame:
    df = pd.read_csv(path, **read_csv_kwargs)
    return df


# ----------------------------
# Example (optional)
# ----------------------------

def _example_oracle() -> None:
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "created_at": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-02-01"]),
        "payload": [{"a": 1}, {"b": 2}, None],
    })

    part = OraclePartitionSpec(
        kind="range",
        expr=["created_at"],
        partitions=[
            {"name": "p2024_01", "values": "TO_DATE('2024-02-01','YYYY-MM-DD')"},
            {"name": "pmax", "values": "MAXVALUE"},
        ],
    )

    _, ddl, meta = df_to_ddl_and_schema(
        df, "events", "oracle",
        schema_name="HR",
        pk="id",
        autoincrement=AutoIncrement(column="id", initial_value=1),
        oracle_partition=part,
    )

    print(ddl)
    print(schema_to_json_text(meta))


def _example_mssql() -> None:
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "tenant_id": [10, 20, 30],
        "created_at": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-02-01"]),
    })

    part = MsSqlPartitionSpec(
        column="tenant_id",
        boundary_values=["100", "200", "300"],
        range_right=True,
        data_space="PRIMARY",
    )

    _, ddl, meta = df_to_ddl_and_schema(
        df, "events", "mssql",
        schema_name="dbo",
        pk="id",
        autoincrement=AutoIncrement(column="id", initial_value=1),
        mssql_partition=part,
    )

    print("-- Pre-script (run first):")
    print(meta["mssql_partition"]["pre_script"])
    print("\n-- Create table:")
    print(ddl)


if __name__ == "__main__":
    _example_oracle()
    _example_mssql()