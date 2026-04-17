# SqlPen Pipeline Modules 🔧

_Complete reference for all modules in `pipeline/`_
_Source: [github.com/hereisomi/sqlpen](https://github.com/hereisomi/sqlpen)_

The `pipeline/` package is the top-level execution layer of SqlPen. It contains five
modules that cover every data ingestion scenario: single DataFrame loads, dictionary
payloads, CSV diagnostic testing, incremental directory watching, and Oracle metadata auditing.

> Related docs: [cli.md](cli.md) · [harness.md](harness.md) · [peek.md](peek.md) · [Back to README](../README.md)

---

## Table of Contents

- [Modules Overview](#modules-overview)
- [df_tosql — ETL Pipeline Facade](#df_tosql--etl-pipeline-facade)
- [dict_tosql — Dictionary Pipeline Facade](#dict_tosql--dictionary-pipeline-facade)
- [csv_harness — Diagnostic DML Harness](#csv_harness--diagnostic-dml-harness)
- [csvdog — Incremental Directory Watchdog](#csvdog--incremental-directory-watchdog)
- [oracle_monitor — Oracle Metadata Freshness Monitor](#oracle_monitor--oracle-metadata-freshness-monitor)
- [Shared Preprocessing Pipeline](#shared-preprocessing-pipeline)
- [CrudResult — Return Type](#crudresult--return-type)

---

## Modules Overview

| Module | Public API | Purpose |
|--------|-----------|---------|
| `df_tosql.py` | `df_tosql()` | Load DataFrame / file / URL into SQL |
| `dict_tosql.py` | `dict_tosql()` | Load dict or list of dicts into SQL |
| `csv_harness.py` | `run_csv_pipeline()`, `PipelineRunner`, `PipelineReport` | Diagnostic INSERT → UPSERT → UPDATE test cycle |
| `csvdog.py` | `csvdog()` | Incremental directory watchdog with mtime tracking |
| `oracle_monitor.py` | `run_oracle_audit()` | Oracle Data Dictionary freshness audit |

---

## df_tosql — ETL Pipeline Facade

**File:** `pipeline/df_tosql.py`

The primary ETL entry point. Accepts a DataFrame, local file, or remote URL and
orchestrates the full preprocessing + CRUD execution pipeline.

### Supported Source Formats

| Format | Extensions | Notes |
|--------|-----------|-------|
| CSV | `.csv`, `.tsv`, `.txt` | Tab-separated auto-detected for `.tsv` |
| Parquet | `.parquet`, `.pq` | |
| JSON | `.json` | |
| Excel | `.xlsx`, `.xls` | |
| Remote URL | `http://`, `https://` | Pandas handles natively |
| S3 | `s3://` | Requires `s3fs` installed |
| DataFrame | — | Passed through directly |

> Files larger than **200 MB** are read in chunks automatically using `tqdm` progress if available.

### Signature

```python
def df_tosql(
    df: Union[pd.DataFrame, str, Path],
    table: str,
    engine: Optional[Engine] = None,
    if_exist: str = 'insert',        # 'insert' | 'replace' | 'upsert' | 'update'
    schema: Optional[str] = None,
    chunk: int = 1000,
    constraint_cols: Union[List[str], str, None] = '',
    where: Optional[List[Any]] = None,
    expression: Optional[str] = None,
    add_new_column: bool = True,
    clean: bool = True,
    cast: bool = True,
    auto_profiling: bool = False,
    outlier: float = 0.5,
    schema_name: str = 'abc.json'
) -> CrudResult
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `df` | DataFrame, str, Path | required | Source data — DataFrame, file path, or URL |
| `table` | str | required | Target table name |
| `engine` | Engine | None | SQLAlchemy engine. Auto-resolved from `DATABASE_URL` if None |
| `if_exist` | str | `'insert'` | Write mode: `insert`, `replace`, `upsert`, `update` |
| `schema` | str | None | Database schema/namespace |
| `chunk` | int | `1000` | Batch size for bulk operations |
| `constraint_cols` | str or list | `''` | Unique constraint columns for upsert (comma-separated string or list) |
| `where` | list | None | WHERE conditions for update mode — required when `if_exist='update'` |
| `expression` | str | None | Logical expression combining WHERE conditions e.g. `"1 AND (2 OR 3)"` |
| `add_new_column` | bool | `True` | ALTER TABLE to add columns present in df but missing in table |
| `clean` | bool | `True` | Sanitize column names (lowercase, remove special chars) |
| `cast` | bool | `True` | Auto-cast types (dates, booleans, numerics) |
| `auto_profiling` | bool | `False` | Infer PK from data profile (only used for upsert) |
| `outlier` | float | `0.5` | IQR outlier threshold — set to `0` to disable |
| `schema_name` | str | `'abc.json'` | File path to dump schema metadata as JSON. Set to `''` to skip |

### Write Modes

| Mode | Table exists | Table missing | Behaviour |
|------|-------------|---------------|-----------|
| `insert` | Appends rows | Creates then inserts | Standard append |
| `replace` | Drops, recreates, inserts | Creates then inserts | Full table refresh |
| `upsert` | Insert new / update existing | Creates then upserts | Idempotent load |
| `update` | Updates matching rows | Raises `ValueError` | Targeted row updates |

### Internal Pipeline Steps

```
1. Resolve engine      →  get_engine_from_env() if engine is None
2. Load source         →  _load_source() — file/URL/DataFrame → DataFrame
3. Validate            →  check if_exist value, check where for update mode
4. Clean               →  sanitize_dataframe_columns() [if clean=True]
5. Cast                →  auto_cast() [if cast=True]
6. Outlier quarantine  →  replace_outliers_with_zero_safe() [if outlier > 0]
7. Auto-profile        →  profile_dataframe() + get_pk() [if auto_profiling=True]
8. Schema JSON dump    →  df_to_ddl_and_schema() → write JSON [if schema_name set]
9. Table lifecycle     →  ensure_table() — create/drop/skip
10. CRUD execution     →  auto_insert / auto_upsert / auto_update
```

### Examples

```python
from SqlPen import df_tosql, get_engine_from_env

engine = get_engine_from_env("postgresql://user:pass@localhost/mydb")

# Insert from CSV (auto-creates table)
result = df_tosql("users.csv", table="users", engine=engine)

# Upsert from DataFrame
import pandas as pd
df = pd.read_csv("users.csv")
result = df_tosql(df, table="users", engine=engine,
                  if_exist="upsert", constraint_cols="id")

# Replace table entirely
result = df_tosql("users.csv", table="users", engine=engine,
                  if_exist="replace")

# Update with WHERE conditions
result = df_tosql(df, table="users", engine=engine,
                  if_exist="update",
                  where=[("id", "=", "?"), ("active", "=", 1)])

# Update with complex expression
result = df_tosql(df, table="users", engine=engine,
                  if_exist="update",
                  where=[("id", "=", "?"), ("status", "=", "active")],
                  expression="1 AND 2")

# Load from remote URL
result = df_tosql("https://example.com/data.csv", table="events", engine=engine)

# Load from S3
result = df_tosql("s3://my-bucket/data.parquet", table="events", engine=engine,
                  if_exist="upsert", constraint_cols="event_id")

# Dump schema metadata
result = df_tosql("users.csv", table="users", engine=engine,
                  schema_name="./schema/users.json")

# Composite constraint upsert
result = df_tosql("orders.csv", table="orders", engine=engine,
                  if_exist="upsert", constraint_cols="order_id,line_id")

# Disable all preprocessing
result = df_tosql(df, table="raw", engine=engine,
                  clean=False, cast=False, outlier=0)

print(f"Success: {result.success}, Failed: {result.failed}, Method: {result.method}")
```

---

## dict_tosql — Dictionary Pipeline Facade

**File:** `pipeline/dict_tosql.py`

A thin wrapper around `df_tosql` that accepts Python dictionaries or lists of
dictionaries instead of a DataFrame. Converts the input to a DataFrame internally
and delegates all processing to `df_tosql`.

### Signature

```python
def dict_tosql(
    data: Union[Dict[str, Any], List[Dict[str, Any]]],
    table: str,
    engine: Optional[Engine] = None,
    if_exist: str = 'insert',
    schema: Optional[str] = None,
    chunk: int = 1000,
    constraint_cols: Union[List[str], str, None] = '',
    where: Optional[List[Any]] = None,
    expression: Optional[str] = None,
    add_new_column: bool = True,
    clean: bool = True,
    cast: bool = True,
    auto_profiling: bool = False,
    outlier: float = 0.5,
    schema_name: str = 'abc.json'
) -> CrudResult
```

### Parameters

Identical to `df_tosql` except:

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | dict or list[dict] | A single dict or a list of dicts (JSON-like records) |

All other parameters are passed through directly to `df_tosql`.

### Examples

```python
from SqlPen import dict_tosql, get_engine_from_env

engine = get_engine_from_env("postgresql://user:pass@localhost/mydb")

# Insert a single record
result = dict_tosql(
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    table="users",
    engine=engine
)

# Insert multiple records
records = [
    {"id": 1, "name": "Alice", "email": "alice@example.com"},
    {"id": 2, "name": "Bob",   "email": "bob@example.com"},
]
result = dict_tosql(records, table="users", engine=engine,
                    if_exist="upsert", constraint_cols="id")

# From a JSON API response
import requests
data = requests.get("https://api.example.com/users").json()
result = dict_tosql(data, table="api_users", engine=engine,
                    if_exist="upsert", constraint_cols="id",
                    clean=True, cast=True)

print(f"Success: {result.success}, Failed: {result.failed}")
```

---

## csv_harness — Diagnostic DML Harness

**File:** `pipeline/csv_harness.py`

Orchestrates a deterministic INSERT → UPSERT → UPDATE validation cycle against a
CSV file. Validates the database state after each step and generates a detailed
diagnostic report.

> See [harness.md](harness.md) for the full harness documentation including data
> slicing details, mutation rules, and root cause diagnostics.

### Public API

#### `run_csv_pipeline()` — Convenience entrypoint

```python
def run_csv_pipeline(
    csv_path: str,
    engine: Engine,
    table: str,
    pk_cols: str | list[str],
    constraint_cols: str | list[str],
    config: CrudConfig | None = None,
    schema: str | None = None,
    validate: bool = True,
    report_dir: str | Path | None = None,
    clean: bool = True,
    cast: bool = True,
    outlier: float = 0.5,
    auto_profiling: bool = False,
    **read_csv_kwargs
) -> PipelineReport
```

Reads the CSV, builds a `PipelineRunner`, runs the full cycle, and returns a
`PipelineReport`. Writes `<csv_stem>_harness.txt` to the CSV directory or `report_dir`.

#### `PipelineRunner` — Full control class

```python
runner = PipelineRunner(
    engine=engine,
    source_df=df,
    table="users",
    pk_cols="id",
    constraint_cols="email",
    config=CrudConfig(chunk_size=5000),
    clean=True,
    cast=True,
    outlier=0.5,
    auto_profiling=True,
    report_path="./reports/users_harness.txt",
)

report = runner.run()                  # full INSERT → UPSERT → UPDATE
step   = runner.run_insert_only()      # INSERT only
step   = runner.run_upsert_only()      # UPSERT only (assumes INSERT ran)
step   = runner.run_update_only()      # UPDATE only (assumes INSERT + UPSERT ran)
```

#### `PipelineReport` — Result dataclass

| Field | Type | Description |
|-------|------|-------------|
| `table` | str | Target table name |
| `steps` | list[StepResult] | Results for INSERT, UPSERT, UPDATE steps |
| `fingerprint_before` | SchemaFingerprint | Schema hash before DML |
| `fingerprint_after` | SchemaFingerprint | Schema hash after DML |
| `schema_diff` | SchemaDiff | Detected schema drift |
| `total_elapsed_s` | float | Total wall-clock time |
| `all_passed` | bool (property) | True if all 3 steps validated successfully |

#### `StepResult` — Per-step result dataclass

| Field | Type | Description |
|-------|------|-------------|
| `step` | str | `"INSERT"`, `"UPSERT"`, or `"UPDATE"` |
| `crud_result` | CrudResult | success/failed counts and method |
| `validation_passed` | bool | Whether verification SELECT matched expected state |
| `validation_error` | str | Error message if validation failed |
| `elapsed_s` | float | Step execution time in seconds |
| `rows_sent` | int | Number of rows sent in this step |
| `verification_sql` | str | The SELECT query used for verification |
| `diagnostics` | list[dict] | Root cause analysis entries |

### Examples

```python
from SqlPen import run_csv_pipeline, get_engine_from_env

engine = get_engine_from_env("postgresql://user:pass@localhost/mydb")

# Simple run
report = run_csv_pipeline(
    "data.csv", engine, table="users",
    pk_cols="id", constraint_cols="email"
)
print(report.summary())

# Full preprocessing
report = run_csv_pipeline(
    "telecom.csv", engine, table="cdr_raw",
    pk_cols="record_id", constraint_cols="msisdn",
    clean=True, cast=True, outlier=0.5, auto_profiling=True,
    report_dir="./reports"
)

# Inspect per-step results
for step in report.steps:
    print(f"{step.step}: passed={step.validation_passed}, "
          f"success={step.crud_result.success}, elapsed={step.elapsed_s}s")
    for diag in step.diagnostics:
        print(f"  [{diag['root_cause']}] {diag['recommendation']}")

# Check schema drift
if report.schema_diff and report.schema_diff.changed:
    print(f"Schema drift: {report.schema_diff.summary()}")

# PipelineRunner for step-by-step control
import pandas as pd
from SqlPen import PipelineRunner, CrudConfig

df = pd.read_csv("data.csv")
runner = PipelineRunner(
    engine=engine, source_df=df, table="users",
    pk_cols=["id"], constraint_cols=["email"],
    config=CrudConfig(chunk_size=5000, trace_sql=True),
    clean=True, cast=True
)
insert_result = runner.run_insert_only()
print(f"INSERT: {insert_result.crud_result.success} rows inserted")
```

---

## csvdog — Incremental Directory Watchdog

**File:** `pipeline/csvdog.py`

Scans a directory for CSV files and incrementally loads only those that have changed
since the last run. Tracks file state in a JSON manifest using modification timestamps.
Auto-infers PKs via the profiler for new files.

### Signature

```python
def csvdog(
    filepath: str,
    engine: Engine,
    schema: str = 'mycsv.json',
    chunk: int = 10000,
    outlier: float = 0.5
) -> Dict[str, Any]
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | str | required | Path to the directory containing CSV files |
| `engine` | Engine | required | SQLAlchemy engine |
| `schema` | str | `'mycsv.json'` | Path to the JSON manifest file for tracking state |
| `chunk` | int | `10000` | Batch size for bulk operations |
| `outlier` | float | `0.5` | IQR outlier threshold |

### Returns

`Dict[str, Any]` — a results map of `{table_name: status}` where status is one of:

| Status | Meaning |
|--------|---------|
| `"success"` | File was processed and loaded successfully |
| `"failed"` | File processing failed — error saved to manifest |
| `"skipped"` | File is unchanged since last run — no action taken |

### How It Works

```
For each *.csv in directory:
  │
  ├─ Read mtime from manifest
  ├─ Compare to current file mtime
  │
  ├─ [SKIP] mtime unchanged → log and skip
  │
  └─ [RUN] mtime changed or new file:
       1. pd.read_csv()
       2. sanitize_dataframe_columns()  (clean locally)
       3. Check manifest for known PK
       │
       ├─ PK known → if_exist = "upsert"
       └─ PK unknown → profile_dataframe() + get_pk()
                       → if_exist = "replace" (first-time init)
                       → fallback: first column as PK
       │
       4. df_tosql(clean=False, cast=True, auto_profiling=False)
       5. Update manifest with mtime, pk, status, rows
       6. Write manifest to disk immediately (crash-safe)
```

### JSON Manifest Format

The manifest (`schema` parameter) tracks state per table:

```json
{
    "users": {
        "mtime": 1704067200.0,
        "pk": ["id"],
        "status": "success",
        "file": "data/users.csv",
        "rows": 1500
    },
    "orders": {
        "mtime": 1704070800.0,
        "pk": ["order_id"],
        "status": "failed",
        "file": "data/orders.csv",
        "error": "Missing PK columns: ['order_id']"
    }
}
```

> The manifest is written to disk after **every file** — not at the end — so a crash
> mid-run does not lose progress on already-processed files.

### Skip Rules

| Condition | Action |
|-----------|--------|
| `mtime` unchanged + `status = "success"` | Skip silently |
| `mtime` unchanged + `status = "failed"` | Skip with warning log |
| `mtime` changed or new file | Process |

### Examples

```python
from SqlPen import csvdog, get_engine_from_env

engine = get_engine_from_env("postgresql://user:pass@localhost/mydb")

# Scan a directory and load changed CSVs
results = csvdog(
    filepath="./data",
    engine=engine,
    schema="./data/manifest.json",
    chunk=5000,
    outlier=0.5
)

for table, status in results.items():
    print(f"{table}: {status}")
# users:  success
# orders: skipped
# events: failed

# Re-run — only changed files will be processed
results = csvdog("./data", engine, schema="./data/manifest.json")
```

---

## oracle_monitor — Oracle Metadata Freshness Monitor

**File:** `pipeline/oracle_monitor.py`

An Oracle-specific pipeline that reads the Oracle Data Dictionary to identify
recently active tables, finds their timestamp columns, and probes each table's
data freshness. Designed for telecom and enterprise Oracle environments with
thousands of tables.

> This module **only works with Oracle** engines. Passing any other dialect raises `ValueError`.

### Public API

#### `run_oracle_audit()` — Primary facade

```python
def run_oracle_audit(
    engine: Engine,
    schema: str,
    lookback_days: int = 7,
    throttle_secs: float = 2.0
) -> pd.DataFrame
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `engine` | Engine | required | Oracle SQLAlchemy engine |
| `schema` | str | required | Oracle schema/owner name |
| `lookback_days` | int | `7` | How many days back to look for active table modifications |
| `throttle_secs` | float | `2.0` | Sleep between table probes to avoid overloading the DB |

Returns a `pd.DataFrame` with columns:

| Column | Description |
|--------|-------------|
| `owner` | Oracle schema name (uppercased) |
| `table_name` | Table name |
| `time_column` | Timestamp column used for freshness check |
| `last_update` | Latest value found in the timestamp column |

Returns an empty DataFrame if no active tables or timestamp columns are found.

### Internal Functions

#### `get_active_tables(engine, schema, lookback_days)`
Queries `all_tab_modifications` to find tables with INSERT or UPDATE activity
in the last N days.

#### `find_candidate_time_cols(engine, schema)`
Queries `all_tab_columns` for DATE/TIMESTAMP columns whose names contain
`TIME`, `DATE`, `PERIOD`, `LOAD`, or `EVENT`.

#### `get_latest_partition_value(engine, schema, table_name)`
Attempts to read the latest partition's `high_value` from `all_tab_partitions`
as a fast alternative to a full table scan. Returns `None` if the table is not
partitioned.

#### `probe_freshness(engine, schema, target_df, throttle_secs)`
Iterates over the merged target DataFrame and for each table:
1. Tries `get_latest_partition_value()` first (fast path)
2. Falls back to `SELECT MAX(time_column) FROM schema.table` (full scan)

### Audit Pipeline Flow

```
run_oracle_audit(engine, schema)
   │
   ├─ get_active_tables()
   │    → SELECT from all_tab_modifications WHERE timestamp > SYSDATE - N
   │    → Returns tables with inserts + updates > 0
   │
   ├─ find_candidate_time_cols()
   │    → SELECT from all_tab_columns WHERE data_type IN (DATE, TIMESTAMP...)
   │    → Filters column names matching TIME/DATE/PERIOD/LOAD/EVENT
   │
   ├─ INNER JOIN active_tables + time_cols ON table_name
   │
   └─ probe_freshness()
        For each target table:
          1. Try partition high_value  (fast — no table scan)
          2. Fallback: SELECT MAX(col) (full scan)
          3. Sleep throttle_secs
        → Returns DataFrame with last_update per table
```

### Examples

```python
from SqlPen import run_oracle_audit, get_engine_from_env

engine = get_engine_from_env(
    "oracle+cx_oracle://user:pass@host:1521/ORCL"
)

# Run audit on a schema
freshness_df = run_oracle_audit(
    engine=engine,
    schema="TELECOM",
    lookback_days=7,
    throttle_secs=2.0
)

print(freshness_df)
#    owner       table_name    time_column          last_update
# 0  TELECOM     CDR_RAW       EVENT_TIME   2024-01-15 13:45:00
# 1  TELECOM     SUBSCRIBERS   LOAD_DATE    2024-01-14 08:00:00

# Shorter lookback, faster throttle
freshness_df = run_oracle_audit(engine, "TELECOM",
                                lookback_days=3, throttle_secs=0.5)

# Check for stale tables (no update in 24 hours)
import pandas as pd
freshness_df["last_update"] = pd.to_datetime(freshness_df["last_update"], errors="coerce")
stale = freshness_df[freshness_df["last_update"] < pd.Timestamp.now() - pd.Timedelta(hours=24)]
print(f"Stale tables: {stale['table_name'].tolist()}")
```

---

## Shared Preprocessing Pipeline

All pipeline modules that accept `clean`, `cast`, `outlier`, and `auto_profiling`
run the same preprocessing steps in the same fixed order:

```
Step 1 — Clean (if clean=True)
   sanitize_dataframe_columns(server=dialect, allow_space=False, to_lower=True)
   → Lowercases all column names
   → Replaces spaces and special characters with underscores
   → Remaps pk/constraint column names through the same mapping

Step 2 — Cast (if cast=True)
   auto_cast(use_patterns=True)
   → Infers and converts string columns to dates, booleans, integers, floats
   → Uses regex pattern matching for date formats

Step 3 — Outlier Quarantine (if outlier > 0)
   replace_outliers_with_zero_safe(method='iqr', threshold=outlier)
   → IQR-based detection on numeric columns
   → Replaces extreme values with zero

Step 4 — Auto-Profile (if auto_profiling=True)
   profile_dataframe() + get_pk()
   → Profiles column cardinality, uniqueness, null rates
   → Infers the most likely primary key column
   → Only activates PK inference if pk_cols is empty or ['id']
```

---

## CrudResult — Return Type

All pipeline functions return a `CrudResult` dataclass:

```python
@dataclass
class CrudResult:
    total: int       # total rows attempted
    success: int     # rows successfully written
    failed: int      # rows that failed
    method: str      # "bulk" | "row_fallback" | "none"
    diagnostics: dict
```

The `method` field indicates how the data was written:

| Method | Meaning |
|--------|---------|
| `"bulk"` | All rows written in a single bulk operation |
| `"row_fallback"` | Bulk failed; rows written one-by-one with per-row error collection |
| `"none"` | No rows were processed (empty input) |

```python
result = df_tosql("data.csv", table="users", engine=engine)

print(result.total)       # 1000
print(result.success)     # 998
print(result.failed)      # 2
print(result.method)      # "row_fallback"
print(result.diagnostics) # {"bulk_error": "...", "row_errors": [...]}
```
