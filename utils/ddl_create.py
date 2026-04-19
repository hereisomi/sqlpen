# dataframe_ddl.py
import re
import json
import pandas as pd
from keyword import iskeyword

ORACLE_MAX_IDENTIFIER = 30
DEFAULT_VARCHAR_SIZE = 255

RESERVED_WORDS = {
    "oracle": {"SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "GROUP", "ORDER", "COUNT", "BY", "CREATE", "TABLE", "INDEX", "VIEW", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT", "NUMBER", "DATE", "UNION", "ALL", "DISTINCT", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "ON", "USING", "HAVING", "LIMIT", "OFFSET", "ALTER", "DROP", "TRUNCATE", "TO", "DESC", "ASC", "LIMIT", "OFFSET", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "ON", "USING", "HAVING", "UNION", "ALL", "DISTINCT", "EXISTS", "IN", "ANY", "SOME", "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "EXTRACT", "SUBSTRING", "TRIM", "UPPER", "LOWER", "LIKE", "SIMILAR", "BETWEEN", "IS", "NULL", "TRUE", "FALSE", "VALUE"},
    "postgresql": {"SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "GROUP", "ORDER", "BY", "CREATE", "TABLE", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT", "REFERENCES", "BIGINT", "INTEGER", "SMALLINT", "VARCHAR", "TIMESTAMP", "BOOLEAN", "NUMERIC", "DECIMAL", "REAL", "DESC", "ASC", "LIMIT", "OFFSET", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT", "ON", "USING", "HAVING", "UNION", "ALL", "DISTINCT", "EXISTS", "IN", "ANY", "SOME", "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "EXTRACT", "SUBSTRING", "TRIM", "UPPER", "LOWER", "ILIKE", "LIKE", "SIMILAR", "BETWEEN", "IS", "NULL", "TRUE", "FALSE"},
    "mysql": {"SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "TABLE", "CREATE", "DROP", "ALTER", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT", "INT", "VARCHAR", "DATETIME", "BIGINT", "SMALLINT", "AUTO_INCREMENT", "UNIQUE", "INDEX"},
    "mssql": {"SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "TABLE", "CREATE", "DROP", "ALTER", "PRIMARY", "KEY", "FOREIGN", "CONSTRAINT", "IDENTITY", "INT", "VARCHAR", "DATETIME", "BIT", "FLOAT", "NUMERIC", "DECIMAL", "BIGINT", "SMALLINT"},
    "sqlite": {"SELECT", "INSERT", "DELETE", "UPDATE", "WHERE", "FROM", "TABLE", "CREATE", "DROP", "ALTER", "CONSTRAINT", "PRIMARY", "KEY", "FOREIGN", "UNIQUE", "CHECK", "DEFAULT"},
}

DTYPE_MAP = {
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


def _normalize_server(server):
    s = (server or "").lower()
    if s == "postgres":
        s = "postgresql"
    return s


def _truncate_identifier(name, max_len):
    out = name[:max_len] if len(name) > max_len else name
    return out


def _reserved_set(dialect):
    d_key = _normalize_server(dialect)
    base = {kw.upper() for kw in RESERVED_WORDS.get(d_key, set())}
    extras = {"SELECT", "FROM", "WHERE", "GROUP", "ORDER", "LIMIT", "JOIN", "TABLE", "COLUMN", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "SCHEMA", "INDEX", "VIEW", "TRIGGER", "PROCEDURE", "FUNCTION", "DATABASE", "USER", "ROLE", "GRANT", "REVOKE"}
    out = base | extras
    return out


def sanitize_cols(obj, allow_space=True, to_lower=True, fallback="col_", dialect="postgresql"):
    """
    Cleans and normalizes column names or keys.
    Spaces may be preserved; DDL generation will quote identifiers containing spaces.
    """
    d_key = _normalize_server(dialect)
    sql_kw = _reserved_set(d_key)
    blank_count = iter(range(1, 1_000_000))

    def clean_one(name):
        s = str(name).strip()
        pattern = r"[^\w\s]" if allow_space else r"[^\w]"
        s = re.sub(pattern, "_", s)
        if allow_space:
            s = re.sub(r"\s+", " ", s)
        if to_lower:
            s = s.lower()
        s = re.sub(r"__+", "_", s).strip("_")
        if re.match(r"^\d", s):
            if d_key == "oracle":
                s = "N" + s
            else:
                s = f"_{fallback}{next(blank_count)}"
        if not s:
            s = f"{fallback}{next(blank_count)}"
        if s.upper() in sql_kw or iskeyword(s):
            s = s + "_"
        return s

    def clean_many(names):
        seen = {}
        out = []
        for n in names:
            c = clean_one(n)
            idx = seen.get(c, 0)
            seen[c] = idx + 1
            out.append(c if idx == 0 else f"{c}_{idx}")
        return out

    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
        df.columns = clean_many(df.columns.tolist())
        return df
    if isinstance(obj, dict):
        keys = clean_many(list(obj.keys()))
        return dict(zip(keys, obj.values()))
    if isinstance(obj, (list, tuple, pd.Index)):
        return clean_many(list(obj))
    if isinstance(obj, str):
        return clean_one(obj)
    return obj


def escape_identifier(name, server="oracle"):
    d_key = _normalize_server(server)
    reserved = RESERVED_WORDS.get(d_key, RESERVED_WORDS["oracle"])
    name_upper = str(name).upper()
    name = str(name)

    if d_key == "oracle":
        simple = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", name) is not None
        if simple and name_upper not in reserved:
            out = name_upper
            return out
        safe_name = name.replace('"', '""')
        return f'"{safe_name}"'

    if d_key == "postgresql":
        # Quote reserved words and mixed-case identifiers; lower-case simple names stay unquoted
        simple = re.fullmatch(r"[a-z_][a-z0-9_$]*", name) is not None
        if simple and name_upper not in reserved:
            return name
        safe_name = name.replace('"', '""')
        return f'"{safe_name}"'

    if d_key == "sqlite":
        simple = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", name) is not None
        if simple and name_upper not in reserved:
            return name
        safe_name = name.replace('"', '""')
        return f'"{safe_name}"'

    if d_key == "mysql":
        # Always quote to avoid reserved-word conflicts and case sensitivity issues
        return f"`{name.replace('`', '``')}`"

    if d_key == "mssql":
        return f"[{name}]"

    return name


def _normalize_dtype(dtype):
    s = str(dtype).lower()
    if s in ("int64", "int32", "int16", "int8"):
        return "Int64"
    if s in ("float64", "float32"):
        return "Float64"
    if s == "bool":
        return "boolean"
    return s


def get_sql_type(col_dtype, server, col_name=None, varchar_sizes=None, default_varchar=DEFAULT_VARCHAR_SIZE):
    d_key = _normalize_server(server)
    dtype_map = DTYPE_MAP.get(d_key, DTYPE_MAP["oracle"])
    norm = _normalize_dtype(col_dtype)
    sql_type = dtype_map.get(norm)

    if sql_type:
        return sql_type

    nd = str(norm).lower()
    if any(x in nd for x in ("datetime", "date", "time")):
        return dtype_map.get("datetime", f"VARCHAR({default_varchar})")
    if "timedelta" in nd:
        return dtype_map.get("timedelta", f"VARCHAR({default_varchar})")

    size = default_varchar
    if varchar_sizes and col_name:
        size = varchar_sizes.get(col_name, default_varchar)
    if varchar_sizes and not col_name:
        size = varchar_sizes.get("", default_varchar)

    if d_key == "oracle":
        return f"VARCHAR2({size})"
    if d_key in ("postgresql", "sqlite"):
        return "TEXT"
    return f"VARCHAR({size})"


def _normalize_cols(cols):
    if cols is None:
        out = []
    elif isinstance(cols, str):
        out = [cols]
    else:
        out = list(cols)
    return out


def _validate_cols_exist(df, cols, label):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{label} references non-existent column(s): {missing}")


def _build_pk_constraint(table, pk, server):
    s = _normalize_server(server)
    cols = ", ".join(escape_identifier(x, s) for x in _normalize_cols(pk))
    if s == "sqlite":
        return f"PRIMARY KEY ({cols})"
    name = escape_identifier(_truncate_identifier(f"{table}_PK", ORACLE_MAX_IDENTIFIER), s)
    return f"CONSTRAINT {name} PRIMARY KEY ({cols})"


def _build_fk_constraint(table, col, ref_tab, ref_col, idx, server):
    s = _normalize_server(server)
    col_esc = escape_identifier(col, s)
    ref_tab_esc = escape_identifier(ref_tab, s)
    ref_col_esc = escape_identifier(ref_col, s)
    if s == "sqlite":
        return f"FOREIGN KEY ({col_esc}) REFERENCES {ref_tab_esc}({ref_col_esc})"
    name = escape_identifier(_truncate_identifier(f"{table}_fk{idx}", ORACLE_MAX_IDENTIFIER), s)
    return f"CONSTRAINT {name} FOREIGN KEY ({col_esc}) REFERENCES {ref_tab_esc}({ref_col_esc})"


def _build_unique_constraint(table, cols, idx, server):
    s = _normalize_server(server)
    col_list = ", ".join(escape_identifier(x, s) for x in _normalize_cols(cols))
    if s == "sqlite":
        return f"UNIQUE ({col_list})"
    name = escape_identifier(_truncate_identifier(f"{table}_uq{idx}", ORACLE_MAX_IDENTIFIER), s)
    return f"CONSTRAINT {name} UNIQUE ({col_list})"


def _build_autoincrement_clause(col, server, initial_value):
    s = _normalize_server(server)
    col_esc = escape_identifier(col, s)

    if s == "sqlite":
        return f"{col_esc} INTEGER PRIMARY KEY AUTOINCREMENT"
    if s == "mysql":
        return f"{col_esc} BIGINT AUTO_INCREMENT"
    if s == "mssql":
        return f"{col_esc} BIGINT IDENTITY({initial_value},1)"
    if s == "postgresql":
        return f"{col_esc} SERIAL"
    if s == "oracle":
        return f"{col_esc} NUMBER GENERATED ALWAYS AS IDENTITY (START WITH {initial_value} INCREMENT BY 1)"
    return ""


def _table_name(table, schema, server):
    s = _normalize_server(server)
    tbl = escape_identifier(table, s)
    if not schema:
        return tbl
    sch = escape_identifier(schema, s)
    return f"{sch}.{tbl}"


def _oracle_uppercase_df_and_constraints(df, pk, fk, unique, autoincrement):
    df = df.copy()
    mapping = {c: str(c).upper() for c in df.columns}
    df.columns = [mapping[c] for c in df.columns]

    def up_cols(x):
        if x is None:
            return None
        if isinstance(x, str):
            return x.upper()
        return [str(c).upper() for c in x]

    pk = up_cols(pk)

    if fk:
        fk = [(str(c).upper(), rt, rc) for (c, rt, rc) in fk]
    if unique:
        out = []
        for u in unique:
            if isinstance(u, str):
                out.append(u.upper())
            else:
                out.append([str(c).upper() for c in u])
        unique = out
    if autoincrement:
        col, val = autoincrement
        autoincrement = (str(col).upper(), val)

    return df, pk, fk, unique, autoincrement


def _auto_add_indexes(table, server, indexes, pk, autoincrement, index_pk, index_autoincrement):
    s = _normalize_server(server)
    indexes = [] if indexes is None else list(indexes)
    pk_cols = _normalize_cols(pk)
    auto_col = autoincrement[0] if autoincrement else None

    def has_same(cols, unique):
        cols_key = tuple(_normalize_cols(cols))
        for ix in indexes:
            ix_cols = tuple(_normalize_cols(ix.get("columns")))
            if ix_cols == cols_key and bool(ix.get("unique", False)) == bool(unique):
                return True
        return False

    def add(name, cols, unique):
        if has_same(cols, unique):
            return None
        ix_name = name
        if s == "oracle":
            ix_name = _truncate_identifier(ix_name, ORACLE_MAX_IDENTIFIER)
        indexes.append({"name": ix_name, "columns": list(cols), "unique": bool(unique)})
        return None

    if pk_cols and index_pk:
        add(f"{table}_pk_ix", pk_cols, True)

    if auto_col and index_autoincrement:
        if not pk_cols or auto_col not in pk_cols:
            add(f"{table}_{auto_col}_ix", [auto_col], False)

    return indexes


def build_create_table_statement(df, table, schema, pk, fk, unique, autoincrement, server, varchar_sizes=None, default_varchar=DEFAULT_VARCHAR_SIZE, partition=None):
    s = _normalize_server(server)
    cols = []
    auto_col = autoincrement[0] if autoincrement else None
    pk_cols = _normalize_cols(pk)
    escaped = {c: escape_identifier(c, s) for c in df.columns}

    for col in df.columns:
        col_dtype = str(df[col].dtype)
        sql_type = get_sql_type(col_dtype, s, col_name=col, varchar_sizes=varchar_sizes, default_varchar=default_varchar)
        col_esc = escaped[col]
        is_autoinc = auto_col is not None and col == auto_col

        if is_autoinc:
            inline = _build_autoincrement_clause(col, s, autoincrement[1])
            if inline:
                cols.append(inline + " NOT NULL")
                continue

        # PK columns must be NOT NULL for MSSQL; ignore NaNs for PK definition
        if col in pk_cols:
            nullable = "NOT NULL"
        else:
            nullable = "NOT NULL" if not df[col].isna().any() else "NULL"
        cols.append(f"{col_esc} {sql_type} {nullable}")

    add_pk = bool(pk_cols)
    if add_pk:
        is_single_auto_pk = len(pk_cols) == 1 and pk_cols[0] == auto_col
        if is_single_auto_pk and s in ("sqlite", "mysql", "mssql", "postgresql", "oracle"):
            add_pk = False
    if add_pk:
        cols.append(_build_pk_constraint(table, pk, s))

    if fk:
        for i, (c, rt, rc) in enumerate(fk, 1):
            cols.append(_build_fk_constraint(table, c, rt, rc, i, s))

    if unique:
        for i, ug in enumerate(unique, 1):
            cols.append(_build_unique_constraint(table, ug, i, s))

    tbl_name = _table_name(table, schema, s)
    if s == "oracle":
        # Single-line DDL for Oracle; no internal newlines or extra spaces
        cols_joined = ', '.join(' '.join(col.split()) for col in cols)
        ddl = f"CREATE TABLE {tbl_name} ({cols_joined})"
        # Ensure absolutely no newlines anywhere
        ddl = ' '.join(ddl.split())
    else:
        cols_joined = ',\n    '.join(cols)
        ddl = "CREATE TABLE {0} (\n    {1}\n)".format(tbl_name, cols_joined)
        if partition:
            clause = partition.get("clause") if isinstance(partition, dict) else str(partition)
            if clause and str(clause).strip():
                ddl = ddl + "\n" + str(clause).strip()
    return ddl.rstrip(';')


def build_index_statements(table, schema, indexes, server):
    s = _normalize_server(server)
    if not indexes:
        return []

    tbl_name = _table_name(table, schema, s)
    stmts = []

    for i, ix in enumerate(indexes, 1):
        cols = ix.get("columns")
        if not cols:
            raise ValueError(f"Index {i} missing 'columns'")

        col_list = ", ".join(escape_identifier(c, s) for c in _normalize_cols(cols))
        unique = bool(ix.get("unique", False))
        name = ix.get("name") or f"{table}_ix{i}"
        if s == "oracle":
            name = _truncate_identifier(name, ORACLE_MAX_IDENTIFIER)

        name_esc = escape_identifier(name, s)
        uniq_sql = "UNIQUE " if unique else ""
        stmts.append(f"CREATE {uniq_sql}INDEX {name_esc} ON {tbl_name} ({col_list})")

    return stmts


def build_schema_json(df, table, schema, pk, fk, unique, autoincrement, server, default_varchar=DEFAULT_VARCHAR_SIZE, partition=None, indexes=None):
    s = _normalize_server(server)
    columns = []

    for col in df.columns:
        col_dtype = str(df[col].dtype)
        sql_type = get_sql_type(col_dtype, s, col_name=col, default_varchar=default_varchar)
        nullable = bool(df[col].isna().any())

        sample = None
        non_null = df[col].dropna()
        if not non_null.empty:
            sample = str(non_null.iloc[0])

        columns.append({"name": col, "pandas_dtype": col_dtype, "sql_dtype": sql_type, "nullable": nullable, "sample_value": sample})

    pk_cols = _normalize_cols(pk)
    fk_list = [{"column": c, "references_table": rt, "references_column": rc} for c, rt, rc in (fk or [])]
    unique_list = [_normalize_cols(u) for u in (unique or [])]
    auto_meta = {"column": autoincrement[0], "initial_value": autoincrement[1]} if autoincrement else None

    meta = {
        "server": s,
        "schema": schema,
        "table": table,
        "columns": columns,
        "primary_key": pk_cols,
        "foreign_keys": fk_list,
        "unique_constraints": unique_list,
        "autoincrement": auto_meta,
        "partition": partition,
        "indexes": indexes or [],
        "row_count": len(df),
        "column_count": len(df.columns),
    }
    return meta


def df_ddl(input_df_or_csv, table, server="oracle", schema=None, pk=None, fk=None, unique=None, autoincrement=None, default_varchar=DEFAULT_VARCHAR_SIZE, varchar_sizes=None, sanitize=False, partition=None, indexes=None, index_pk=False, index_autoincrement=True):
    server = _normalize_server(server)
    if server not in DTYPE_MAP:
        raise ValueError(f"Unsupported server: {server}")

    if isinstance(input_df_or_csv, str):
        df = pd.read_csv(input_df_or_csv)
    elif isinstance(input_df_or_csv, pd.DataFrame):
        df = input_df_or_csv
    else:
        raise TypeError("First argument must be a pandas DataFrame or CSV file path")

    if df.empty:
        raise ValueError("DataFrame is empty")
    if not table or not isinstance(table, str):
        raise ValueError("Table name must be non-empty string")

    if sanitize or server == "oracle":
        # For Oracle, always sanitize first (no spaces) before any DDL generation
        orig_cols = df.columns.tolist()
        allow_space = False if server == "oracle" else True
        to_lower = False if server == "oracle" else True
        df = sanitize_cols(df, allow_space=allow_space, to_lower=to_lower, dialect=server)
        mapping = dict(zip(orig_cols, df.columns.tolist()))

        def map_one(x):
            out = mapping.get(x, x)
            return out

        def map_many(xs):
            out = [mapping.get(c, c) for c in xs]
            return out

        if pk:
            pk = map_one(pk) if isinstance(pk, str) else map_many(pk)
        if fk:
            fk = [(map_one(c), rt, rc) for (c, rt, rc) in fk]
        if unique:
            out = []
            for u in unique:
                if isinstance(u, str):
                    out.append(map_one(u))
                else:
                    out.append(map_many(u))
            unique = out
        if autoincrement:
            col, val = autoincrement
            autoincrement = (map_one(col), val)

    if server == "oracle":
        df, pk, fk, unique, autoincrement = _oracle_uppercase_df_and_constraints(df, pk, fk, unique, autoincrement)

        # Oracle DDL generation block
        if autoincrement:
            col, val = autoincrement
            if col not in df.columns:
                raise ValueError(f"Autoincrement column '{col}' not in DataFrame")
            if not pd.api.types.is_integer_dtype(df[col].dtype):
                raise ValueError("Autoincrement column must be integer type")

        if fk:
            for i, (c, rt, rc) in enumerate(fk, 1):
                _validate_cols_exist(df, [c], f"Foreign key {i}")

        if unique:
            for i, u in enumerate(unique, 1):
                cols = _normalize_cols(u)
                _validate_cols_exist(df, cols, f"Unique constraint {i}")
                if len(set(cols)) != len(cols):
                    raise ValueError(f"Duplicate column in unique constraint {i}: {cols}")

        indexes = _auto_add_indexes(table, server, indexes, pk, autoincrement, index_pk, index_autoincrement)
        if indexes:
            for i, ix in enumerate(indexes, 1):
                _validate_cols_exist(df, _normalize_cols(ix.get("columns")), f"Index {i}")

        # Ensure DataFrame columns are sanitized for Oracle DDL generation
        from utils.ddl_orc import _sanitize, build_create_table as _orc_build
        df.columns = [_sanitize(c) for c in df.columns]
        ddl_table = _orc_build(df, table, pk=pk)
        ddl_table = " ".join(ddl_table.split())
        ddl_indexes = []
        ddl = ddl_table + ";"

    else:
        indexes = _auto_add_indexes(table, server, indexes, pk, autoincrement, index_pk, index_autoincrement)
        ddl = build_create_table_statement(df, table, schema, pk, fk, unique, autoincrement, server, varchar_sizes, default_varchar, partition)
        ddl_indexes = build_index_statements(table, schema, indexes, server)

    return ddl, ddl_indexes, df


def df_to_schema_class(df, table_name, class_name=None, pk=None, autoincrement=None, sanitize=False):
    """
    If you preserve spaces in column names, the emitted attributes may be invalid Python.
    """
    if df.empty:
        raise ValueError("Input DataFrame is empty")

    work_df = df.copy()
    if sanitize:
        orig_cols = work_df.columns.tolist()
        work_df = sanitize_cols(work_df, allow_space=True)
        mapping = dict(zip(orig_cols, work_df.columns.tolist()))

        def map_one(x):
            out = mapping.get(x, x)
            return out

        if pk:
            pk = map_one(pk) if isinstance(pk, str) else [map_one(c) for c in pk]
        if autoincrement:
            autoincrement = map_one(autoincrement)

    if not class_name:
        class_name = "".join(x.title() for x in table_name.split("_"))

    pk_cols = [pk] if isinstance(pk, str) else (_normalize_cols(pk))
    lines = [
        "from sqlalchemy import Column, BigInteger, Float, Boolean, String, DateTime, Date, Time, Interval",
        "from sqlalchemy.orm import declarative_base",
        "",
        "Base = declarative_base()",
        "",
        f"class {class_name}(Base):",
        f"    __tablename__ = '{table_name}'",
        "",
    ]

    for col in work_df.columns:
        dtype = str(work_df[col].dtype).lower()
        if "int" in dtype:
            sa_type = "BigInteger"
        elif "float" in dtype or "double" in dtype:
            sa_type = "Float"
        elif "bool" in dtype:
            sa_type = "Boolean"
        elif "datetime" in dtype:
            sa_type = "DateTime"
        elif "date" in dtype:
            sa_type = "Date"
        elif "time" in dtype:
            sa_type = "Time"
        elif "timedelta" in dtype or "interval" in dtype:
            sa_type = "Interval"
        else:
            sa_type = "String(255)"

        attrs = []
        if col in pk_cols:
            attrs.append("primary_key=True")
        if col == autoincrement:
            attrs.append("autoincrement=True")
        if not work_df[col].isna().any():
            attrs.append("nullable=False")

        attr_str = ", ".join(attrs)
        if attr_str:
            lines.append(f"    {col} = Column({sa_type}, {attr_str})")
        else:
            lines.append(f"    {col} = Column({sa_type})")

    return "\n".join(lines)