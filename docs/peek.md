# SqlPen peek.py Reference 🔍

_Full database introspection, query execution, schema analysis, and DataFrame alignment_
_Source: [github.com/hereisomi/sqlpen](https://github.com/hereisomi/sqlpen)_

`peek.py` is SqlPen's unified introspection layer. It exposes every database inspection
and DataFrame alignment capability through a single, stateless import — no engine
management required. Just pass a URL (or rely on `DATABASE_URL`) and call the function.

> Related docs: [cli.md](cli.md) · [harness.md](harness.md) · [pipeline.md](pipeline.md) · [Back to README](../README.md)

---

## Table of Contents

- [Connection Resolution](#connection-resolution)
- [Quick Reference](#quick-reference)
- [get_engine](#get_engine)
- [tables / show_tables](#tables--show_tables)
- [describe](#describe)
- [describe_full / table_info](#describe_full--table_info)
- [has_table / table_exists](#has_table--table_exists)
- [get_pk](#get_pk)
- [validate_upsert](#validate_upsert)
- [query](#query)
- [query_clean](#query_clean)
- [get_manager](#get_manager)
- [analyze](#analyze)
- [align](#align)
- [align_df](#align_df)
- [Utility Functions](#utility-functions)
- [Re-exported Dataclasses](#re-exported-dataclasses)
- [Full Workflow Examples](#full-workflow-examples)

---

## Connection Resolution

Every function in `peek.py` accepts an optional `url` parameter. If omitted, the URL
is resolved in this order:

```
1. --url / url argument  (highest priority)
2. DATABASE_URL environment variable
3. DATABASE_URL key in .env file (CWD or project root)
```

Supported URL formats:

| Dialect | URL |
|---------|-----|
| SQLite | `sqlite:///path/to/file.db` or `sqlite:///:memory:` |
| PostgreSQL | `postgresql://user:pass@host:5432/dbname` |
| MySQL / MariaDB | `mysql+pymysql://user:pass@host/dbname` |
| Oracle | `oracle+cx_oracle://user:pass@host:1521/SID` |
| SQL Server | `mssql+pyodbc://user:pass@host/db?driver=ODBC+Driver+17+for+SQL+Server` |

```bash
# .env file
DATABASE_URL=postgresql://user:pass@localhost:5432/mydb
```

---

## Quick Reference

```python
import peek as pk

# --- Engine ---
engine = pk.get_engine("sqlite:///my.db")

# --- Stateless introspection ---
pk.tables()                          # list all tables
pk.describe("users")                 # column DataFrame
pk.describe_full("users")            # PKs, constraints, identity cols
pk.has_table("users")                # bool
pk.get_pk("users")                   # ['id']
pk.validate_upsert("users", ["id"])  # raises if no PK/UNIQUE

# --- Query ---
df = pk.query("SELECT * FROM users WHERE id = :id", params={"id": 1})
df = pk.query_clean("SELECT * FROM users")  # sanitized column names

# --- Rich introspection (SchemaManager) ---
mgr = pk.get_manager()
mgr.list_schemas()
mgr.list_views(schema="public")
mgr.list_tables(pattern="cdr_")
mgr.find_column("msisdn")
mgr.get_table_details("users")
mgr.tail("users", limit=5)
mgr.detect_timestamp_columns("events")
mgr.table_activity_status("events", max_age_days=7)
mgr.classify_table_activity(["events", "users"])
mgr.compare_to_structure("users", {"id": "BIGINT", "name": "VARCHAR(100)"})
mgr.resolve_table("cdr")
mgr.resolve_column("users", "mail")

# --- DataFrame analysis (SchemaAnalyzer) ---
report = pk.analyze("users", df=df)
report.validation.issues
report.mapping.suggestions
report.dialect_checks.suggestions

# --- Strict type alignment (SchemaAligner) ---
aligned = pk.align(df, "users")

# --- Lightweight alignment with report (df_align_to_sql) ---
aligned, report = pk.align_df(df, "users", fix_outliers=True)

# --- Utilities ---
pk.normalize_sql_type(col_type)
pk.detect_outliers(series, method="iqr")
pk.correct_outliers(series, valid_mask)
pk.generate_schema(df)
```

---

## get_engine

```python
pk.get_engine(url: str = None) -> Engine
```

Resolve and return a SQLAlchemy Engine. Useful when you need the engine directly
for use with other SqlPen functions.

```python
import peek as pk

engine = pk.get_engine("postgresql://user:pass@localhost/mydb")
engine = pk.get_engine()  # resolves from DATABASE_URL or .env
```

---

## tables / show_tables

```python
pk.tables(url: str = None, schema: str = None) -> List[str]
```

List all tables in the database. `show_tables` is an alias.

```python
import peek as pk

# All tables
pk.tables("sqlite:///my.db")
# ['users', 'orders', 'products']

# Filter by schema (PostgreSQL / SQL Server)
pk.tables(schema="public")
pk.tables(schema="dbo")

# Using env URL
pk.tables()
```

---

## describe

```python
pk.describe(table: str, url: str = None, schema: str = None) -> pd.DataFrame
```

Returns a DataFrame with column metadata: `name`, `type`, `nullable`, `default`, `autoincrement`.

```python
import peek as pk

df = pk.describe("users")
print(df)
#       name          type  nullable  default  autoincrement
#         id       INTEGER     False     None           True
#       name  VARCHAR(255)      True     None          False
#      email  VARCHAR(255)      True     None          False
#     active       INTEGER      True        1          False
```

Raises `ValueError` if the table does not exist.

---

## describe_full / table_info

```python
pk.describe_full(table: str, url: str = None, schema: str = None) -> Dict[str, Any]
```

Returns a full metadata dictionary including columns, primary keys, unique constraints,
and identity columns. `table_info` is an alias.

```python
import peek as pk

info = pk.describe_full("users")

print(info["table"])               # "users"
print(info["primary_keys"])        # ["id"]
print(info["unique_constraints"])  # [["email"]]
print(info["identity_columns"])    # ["id"]

for col in info["columns"]:
    print(col["name"], col["type"], col["nullable"])
```

---

## has_table / table_exists

```python
pk.has_table(table: str, url: str = None, schema: str = None) -> bool
```

Check if a table exists. `table_exists` is an alias.

```python
import peek as pk

if pk.has_table("users"):
    print("Table exists")

if not pk.table_exists("staging_users"):
    print("Need to create it first")
```

---

## get_pk

```python
pk.get_pk(table: str, url: str = None, schema: str = None) -> List[str]
```

Return the primary key column names for a table.

```python
import peek as pk

pk.get_pk("users")          # ['id']
pk.get_pk("orders")         # ['order_id', 'line_id']  (composite PK)
```

---

## validate_upsert

```python
pk.validate_upsert(table: str, key_cols: List[str], url: str = None, schema: str = None) -> None
```

Validate that the table has a PK or UNIQUE constraint on the given columns before
attempting an upsert. Raises `ValueError` if no suitable constraint is found.

Only enforced for PostgreSQL, MySQL, and SQLite. Oracle and SQL Server MERGE
do not require a constraint.

```python
import peek as pk

# Passes silently if constraint exists
pk.validate_upsert("users", ["id"])

# Raises ValueError if 'score' has no PK/UNIQUE
pk.validate_upsert("users", ["score"])
# ValueError: Upsert requires PK or UNIQUE constraint on ['score'] ...
```

---

## query

```python
pk.query(sql: str, url: str = None, params: dict = None, **kwargs) -> pd.DataFrame
```

Execute a SQL query and return results as a DataFrame. Supports `:param` style
bind variables.

```python
import peek as pk

# Simple query
df = pk.query("SELECT * FROM users LIMIT 10")

# With bind parameters
df = pk.query(
    "SELECT * FROM users WHERE status = :s AND score > :min",
    params={"s": "active", "min": 50.0}
)

# Aggregate
df = pk.query("SELECT status, COUNT(*) as cnt FROM orders GROUP BY status")

# Export to CSV
df = pk.query("SELECT * FROM events")
df.to_csv("events_export.csv", index=False)

# With explicit URL
df = pk.query("SELECT * FROM users", url="postgresql://user:pass@localhost/mydb")
```

---

## query_clean

```python
pk.query_clean(sql: str, url: str = None, params: dict = None,
               clean_columns: bool = True, **kwargs) -> pd.DataFrame
```

Same as `query()` but sanitizes column names after retrieval — lowercases them
and replaces spaces and special characters with underscores.

```python
import peek as pk

# Raw column names from DB: "User ID", "First Name!", "Score%"
df = pk.query_clean("SELECT * FROM legacy_table")
# Cleaned columns: "user_id", "first_name", "score"
print(df.columns.tolist())
```

---

## get_manager

```python
pk.get_manager(url: str = None) -> SchemaManager
```

Return a `SchemaManager` instance bound to the resolved engine. Provides richer
read-only introspection than the stateless functions above.

### SchemaManager methods

| Method | Description |
|--------|-------------|
| `list_schemas()` | List all schemas/namespaces in the database |
| `list_tables(schema, pattern, include_views)` | List tables with optional regex filter |
| `list_views(schema, pattern)` | List views with optional regex filter |
| `find_column(col_pattern, schema)` | Find all tables containing columns matching a pattern |
| `get_table_details(table, schema)` | Deep inspection: columns, PK, FK, indexes, constraints, identity |
| `resolve_table(name_like, schema)` | Fuzzy-match a table name across all schemas |
| `resolve_column(table, col_like, schema)` | Fuzzy-match a column name within a table |
| `detect_timestamp_columns(table, schema)` | Ranked list of timestamp/date columns by name heuristics |
| `table_activity_status(table, schema, max_age_days)` | Check if table is active/stale/empty based on latest timestamp |
| `classify_table_activity(tables, schema, max_age_days)` | Classify multiple tables as active/stale/unknown |
| `compare_to_structure(table, structure, schema)` | Diff table columns against an expected `{col: type}` dict |
| `tail(table, schema, order_by, limit)` | Fetch last N rows ordered by PK or timestamp |

```python
import peek as pk

mgr = pk.get_manager("postgresql://user:pass@localhost/mydb")

# List all schemas
mgr.list_schemas()
# ['public', 'analytics', 'staging']

# List tables matching a pattern
mgr.list_tables(schema="public", pattern="cdr_")
# ['cdr_raw', 'cdr_processed', 'cdr_archive']

# Find all tables with a column named like "msisdn"
mgr.find_column("msisdn", schema="public")
# {'cdr_raw': ['msisdn'], 'subscribers': ['msisdn', 'msisdn_alt']}

# Deep table inspection
details = mgr.get_table_details("users")
details["pk"]          # ['id']
details["fk"]          # [{'constrained_columns': ['dept_id'], ...}]
details["indexes"]     # [{'name': 'idx_users_email', ...}]
details["constraints"] # [{'column_names': ['email']}]

# Fuzzy table resolution
mgr.resolve_table("cdr")
# ('public', 'cdr_raw')

# Fuzzy column resolution
mgr.resolve_column("users", "mail")
# 'email'

# Detect timestamp columns (ranked by relevance)
mgr.detect_timestamp_columns("events")
# ['event_time', 'load_date', 'created_at']

# Check table freshness
status = mgr.table_activity_status("events", max_age_days=7)
# {'table': 'events', 'status': 'active', 'age_days': 2,
#  'timestamp_column': 'event_time', 'max_value': datetime(...), 'row_count': 50000}

# Classify multiple tables
mgr.classify_table_activity(["events", "users", "archive"], max_age_days=30)
# {'events': 'active', 'users': 'active', 'archive': 'stale'}

# Compare table to expected structure
mgr.compare_to_structure("users", {"id": "BIGINT", "name": "VARCHAR(100)", "score": "FLOAT"})
# {'missing_in_db': [], 'extra_in_db': ['email', 'active'], 'type_mismatch': {}}

# Fetch last 5 rows
rows = mgr.tail("users", limit=5)
# [{'id': 100, 'name': 'Alice', ...}, ...]
```

---

## analyze

```python
pk.analyze(
    table: str,
    df: pd.DataFrame = None,
    url: str = None,
    schema: str = None,
    run_fk_checks: bool = False,
) -> TableAnalysisReport
```

Analyze a database table and optionally validate a DataFrame against it.
Returns a `TableAnalysisReport` dataclass.

### TableAnalysisReport fields

| Field | Type | Description |
|-------|------|-------------|
| `engine_info` | `EngineInfo` | Dialect, driver, URL (masked), connection status |
| `table_exists` | `bool` | Whether the table was found |
| `table_name` | `str` | Table name |
| `schema` | `str` | Schema/namespace |
| `table_comment` | `str` | Table comment if set |
| `columns` | `Dict[str, ColumnInfo]` | Per-column metadata |
| `constraints` | `ConstraintInfo` | PKs, FKs, unique constraints, check constraints, indexes |
| `dialect_checks` | `DialectChecks` | Dialect-specific issues and suggestions |
| `df_info` | `DataFrameInfo` | DataFrame dtypes and null counts (if df provided) |
| `mapping` | `MappingInfo` | Column alignment between df and table (if df provided) |
| `validation` | `ValidationSummary` | NOT NULL, UNIQUE, FK violations (if df provided) |

### ColumnInfo fields

| Field | Description |
|-------|-------------|
| `name` | Column name |
| `type_str` | SQLAlchemy type class name |
| `sqlalchemy_type` | Full type string e.g. `VARCHAR(255)` |
| `nullable` | Whether column allows NULL |
| `length` | VARCHAR length limit |
| `precision` / `scale` | Numeric precision and scale |
| `is_numeric` / `is_datetime` / `is_boolean` | Type category flags |
| `issues` / `suggestions` | Per-column diagnostics |

### Dialect-specific checks

| Dialect | What is checked |
|---------|----------------|
| Oracle | Empty string = NULL warning, identifier length > 30 |
| MySQL | TINYINT(1) boolean convention, zero-date values |
| SQL Server | BIT column boolean mapping |
| PostgreSQL | ARRAY and JSON/JSONB column warnings |
| SQLite | Dynamic typing and ALTER TABLE limitations |

```python
import peek as pk
import pandas as pd

# Schema-only analysis (no DataFrame)
report = pk.analyze("users", url="postgresql://user:pass@localhost/mydb")

print(report.engine_info.dialect)          # "postgresql"
print(report.engine_info.connect_ok)       # True

for name, col in report.columns.items():
    print(f"{name}: {col.sqlalchemy_type}, nullable={col.nullable}, length={col.length}")

print(report.constraints.primary_key)      # {'constrained_columns': ['id']}
print(report.constraints.unique_constraints)  # [{'column_names': ['email']}]
print(report.dialect_checks.suggestions)   # dialect-specific advice

# DataFrame validation
df = pd.read_csv("users.csv")
report = pk.analyze("users", df=df)

# Column mapping
print(report.mapping.df_to_sql)            # {'id': 'id', 'name': 'name', ...}
print(report.mapping.missing_sql_cols)     # columns in DB not in df
print(report.mapping.extra_df_cols)        # columns in df not in DB
print(report.mapping.not_null_violations)  # {'name': 3}  — 3 nulls in NOT NULL col
print(report.mapping.length_violations)    # {'email': 2}  — 2 rows exceed VARCHAR limit
print(report.mapping.type_warnings)        # type mismatch warnings
print(report.mapping.suggestions)          # actionable fix suggestions

# Validation summary
print(report.validation.not_null_ok)       # False
print(report.validation.unique_ok)         # True
print(report.validation.issues)            # list of issue strings
print(report.validation.suggestions)       # list of fix suggestions

# FK validation (queries parent tables — use carefully on large DBs)
report = pk.analyze("orders", df=df, run_fk_checks=True)
print(report.validation.fk_violations)     # {'fk_user_id': 5}

# Serialize to dict
d = report.to_dict()
import json
print(json.dumps(d, indent=2, default=str))
```

---

## align

```python
pk.align(
    df: pd.DataFrame,
    table: str,
    url: str = None,
    schema: str = None,
    on_error: str = 'coerce',
    failure_threshold: float = 0.1,
    validate_fk: bool = False,
    add_missing_cols: bool = False,
    col_map: Dict[str, str] = None,
) -> pd.DataFrame
```

Align a DataFrame to a SQL table's schema with **strict type enforcement**.

For each column it:
1. Coerces values to the target SQL type (int, float, string, bool, datetime, JSON, binary)
2. Enforces VARCHAR length limits — values exceeding the limit are nullified
3. Validates NOT NULL constraints
4. Detects outliers via `IsolationForest` and nullifies them if the failure rate exceeds the threshold
5. Optionally adds missing columns via `ALTER TABLE`

Returns the aligned DataFrame with columns ordered to match the table.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `on_error` | `'coerce'` | `'coerce'` — nullify failures and continue. `'raise'` — raise on threshold breach |
| `failure_threshold` | `0.1` | Max fraction of coercion failures (0.1 = 10%) before aborting |
| `validate_fk` | `False` | If True, validate FK integrity before returning |
| `add_missing_cols` | `False` | If True, ALTER TABLE to add df columns missing from the table |
| `col_map` | `None` | Explicit `{df_column: sql_column}` alias mapping |

```python
import peek as pk
import pandas as pd

df = pd.read_csv("users.csv")

# Basic alignment — coerce types, enforce VARCHAR limits
aligned = pk.align(df, "users")

# Strict mode — raise if more than 5% of values fail coercion
aligned = pk.align(df, "users", on_error="raise", failure_threshold=0.05)

# Add missing columns to the table automatically
aligned = pk.align(df, "users", add_missing_cols=True)

# Map df column names to SQL column names
aligned = pk.align(df, "users", col_map={"user_id": "id", "mail": "email"})

# With schema
aligned = pk.align(df, "users", schema="public")

# Full example
aligned = pk.align(
    df, "cdr_raw",
    url="oracle+cx_oracle://user:pass@host:1521/ORCL",
    schema="TELECOM",
    on_error="coerce",
    failure_threshold=0.05,
    add_missing_cols=False,
    col_map={"MSISDN_NO": "msisdn", "EVENT_TS": "event_time"},
)
```

---

## align_df

```python
pk.align_df(
    df: pd.DataFrame,
    table: str,
    url: str = None,
    schema: str = None,
    threshold: float = 10.0,
    fix_outliers: bool = False,
    auto_alter: bool = False,
    outlier_method: str = 'iqr',
) -> Tuple[pd.DataFrame, Dict[str, Any]]
```

Lighter alternative to `align()`. Uses column-level coercion with a null-increase
threshold rather than strict type enforcement. Returns `(aligned_df, report)` for
full observability.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `threshold` | `10.0` | Max allowed null-percentage increase per column before flagging |
| `fix_outliers` | `False` | If True, detect and replace outliers with NA before coercion |
| `auto_alter` | `False` | If True, ALTER TABLE to add extra df columns not in the table |
| `outlier_method` | `'iqr'` | Outlier detection method: `'iqr'` or `'zscore'` |

### Report fields

| Field | Description |
|-------|-------------|
| `columns` | Per-column stats: original/final null counts, cast reliability, errors |
| `missing_columns` | DB columns not present in df (filled with NA) |
| `extra_columns` | df columns not in DB (dropped or added if auto_alter=True) |
| `altered_columns` | Columns added via ALTER TABLE |
| `columns_failed` | Columns where null increase exceeded threshold |
| `outliers_removed` | `{col: count}` of outliers replaced |
| `nulls_introduced_pct` | `{col: pct}` null increase per column |

```python
import peek as pk
import pandas as pd

df = pd.read_csv("events.csv")

# Basic alignment with report
aligned, report = pk.align_df(df, "events")

print(report["missing_columns"])     # ['created_at']  — in DB but not in df
print(report["extra_columns"])       # ['raw_payload'] — in df but not in DB
print(report["columns_failed"])      # ['score']       — too many nulls introduced
print(report["nulls_introduced_pct"])# {'score': 15.3} — 15.3% null increase

# With outlier detection
aligned, report = pk.align_df(df, "events", fix_outliers=True, outlier_method="iqr")
print(report["outliers_removed"])    # {'score': 12}

# With z-score outlier detection
aligned, report = pk.align_df(df, "events", fix_outliers=True, outlier_method="zscore")

# Auto-add extra columns to the table
aligned, report = pk.align_df(df, "events", auto_alter=True)
print(report["altered_columns"])     # ['raw_payload']

# Per-column detail
for col, stats in report["columns"].items():
    print(f"{col}: cast_reliable={stats['cast_reliable']}, "
          f"nulls_added={stats['final_null_pct'] - stats['original_null_pct']:.1f}%")
```

---

## Utility Functions

### normalize_sql_type

```python
pk.normalize_sql_type(col_type: TypeEngine) -> str
```

Map a SQLAlchemy column type to a canonical string: `'int'`, `'float'`, `'str'`,
`'datetime'`, or `'bool'`.

```python
from sqlalchemy import Integer, VARCHAR, DateTime
import peek as pk

pk.normalize_sql_type(Integer())   # 'int'
pk.normalize_sql_type(VARCHAR(255))# 'str'
pk.normalize_sql_type(DateTime())  # 'datetime'
```

### detect_outliers

```python
pk.detect_outliers(series: pd.Series, method: str = 'iqr',
                   iqr_scale: float = 1.5, z_threshold: float = 3.0) -> pd.Series
```

Return a boolean mask where `True` = valid (not an outlier).

```python
import pandas as pd
import peek as pk

s = pd.Series([1, 2, 3, 4, 5, 999])
valid_mask = pk.detect_outliers(s, method="iqr")
# [True, True, True, True, True, False]

valid_mask = pk.detect_outliers(s, method="zscore", z_threshold=2.5)
```

### correct_outliers

```python
pk.correct_outliers(series: pd.Series, valid_mask: pd.Series) -> pd.Series
```

Replace outliers (where `valid_mask=False`) with `pd.NA`.

```python
import peek as pk

valid_mask = pk.detect_outliers(s)
cleaned = pk.correct_outliers(s, valid_mask)
# [1, 2, 3, 4, 5, <NA>]
```

### generate_schema

```python
pk.generate_schema(df: pd.DataFrame, dialect: str = 'postgresql') -> Dict[str, TypeEngine]
```

Infer a `{column_name: SQLAlchemy type}` schema dictionary from a DataFrame.

```python
import pandas as pd
import peek as pk

df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"], "score": [9.5, 8.0]})
schema = pk.generate_schema(df)
# {'id': BigInteger(), 'name': String(255), 'score': Float()}
```

---

## Re-exported Dataclasses

All dataclasses from `schema_analyzer.py` are re-exported from `peek.py` so you
only need a single import:

```python
from peek import (
    TableAnalysisReport,
    ColumnInfo,
    ConstraintInfo,
    MappingInfo,
    ValidationSummary,
    DialectChecks,
    EngineInfo,
)
```

---

## Full Workflow Examples

### Pre-load validation before df_tosql

```python
import peek as pk
import pandas as pd
from SqlPen import df_tosql, get_engine_from_env

engine = get_engine_from_env("postgresql://user:pass@localhost/mydb")
df = pd.read_csv("users.csv")

# 1. Analyze — check for issues before touching the DB
report = pk.analyze("users", df=df, url="postgresql://user:pass@localhost/mydb")

if report.validation.issues:
    for issue in report.validation.issues:
        print(f"[ISSUE] {issue}")
    for suggestion in report.validation.suggestions:
        print(f"[FIX]   {suggestion}")

# 2. Align — fix types and enforce schema
aligned = pk.align(df, "users", url="postgresql://user:pass@localhost/mydb")

# 3. Load — now safe to write
result = df_tosql(aligned, "users", engine=engine, if_exist="upsert",
                  constraint_cols="id", clean=False, cast=False)
print(f"Success: {result.success}, Failed: {result.failed}")
```

### Database health check

```python
import peek as pk

mgr = pk.get_manager("postgresql://user:pass@localhost/mydb")

# Find all tables with timestamp columns
for table in mgr.list_tables(schema="public"):
    ts_cols = mgr.detect_timestamp_columns(table)
    if ts_cols:
        status = mgr.table_activity_status(table, max_age_days=7)
        print(f"{table}: {status['status']} (last update: {status['max_value']})")
```

### Find where a column lives

```python
import peek as pk

mgr = pk.get_manager()

# Find all tables containing a column named like "msisdn"
results = mgr.find_column("msisdn")
for table, cols in results.items():
    print(f"{table}: {cols}")
```

### Schema drift detection

```python
import peek as pk

# Expected structure
expected = {
    "id":    "BIGINT",
    "name":  "VARCHAR(100)",
    "email": "VARCHAR(255)",
    "score": "DOUBLE PRECISION",
}

mgr = pk.get_manager("postgresql://user:pass@localhost/mydb")
diff = mgr.compare_to_structure("users", expected)

if diff["missing_in_db"]:
    print(f"Missing columns: {diff['missing_in_db']}")
if diff["extra_in_db"]:
    print(f"Extra columns:   {diff['extra_in_db']}")
if diff["type_mismatch"]:
    print(f"Type mismatches: {diff['type_mismatch']}")
```

### Outlier detection on a query result

```python
import peek as pk

df = pk.query("SELECT score FROM events WHERE score IS NOT NULL")

valid_mask = pk.detect_outliers(df["score"], method="iqr")
cleaned = pk.correct_outliers(df["score"], valid_mask)

print(f"Outliers removed: {(~valid_mask).sum()}")
print(f"Clean max score:  {cleaned.max()}")
```
