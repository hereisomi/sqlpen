# SqlPen Harness 🔬

_Diagnostic DML Test Harness — INSERT → UPSERT → UPDATE Validation Cycle_
_Source: [github.com/hereisomi/sqlpen](https://github.com/hereisomi/sqlpen)_

The harness is SqlPen's core diagnostic tool. It takes a CSV file, runs a deterministic
3-step DML cycle against a real database, validates the results at each step, and produces
a detailed report mapping every failure to a root cause with an actionable fix.

`sqlpen harness` and `sqlpen test` are identical — both invoke the same command.

> Related docs: [cli.md](cli.md) · [pipeline.md](pipeline.md) · [peek.md](peek.md) · [Back to README](../README.md)

---

## Table of Contents
- [Synopsis](#synopsis)
- [Options](#options)
- [How It Works](#how-it-works)
- [Data Slicing](#data-slicing)
- [Preprocessing Pipeline](#preprocessing-pipeline)
- [Validation & Root Cause Diagnostics](#validation--root-cause-diagnostics)
- [Report Output](#report-output)
- [CLI Examples](#cli-examples)
- [Python API](#python-api)
- [Requirements & Constraints](#requirements--constraints)

---

## Synopsis

```bash
sqlpen harness <SOURCE> [OPTIONS]
sqlpen test    <SOURCE> [OPTIONS]   # identical alias
```

`SOURCE` must be a path to a local CSV file. The harness reads it into a DataFrame,
preprocesses it, slices it deterministically, and executes INSERT → UPSERT → UPDATE
against the target database table.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `SOURCE` | path | required | Path to the source CSV file |
| `--table` | string | inferred from filename | Target database table name |
| `--url` | string | `DATABASE_URL` env / `.env` file | Database connection URL |
| `--pk` | string | `"id"` | Comma-separated primary key column(s) |
| `--constraint` | string | `"id"` | Comma-separated constraint column(s) for UPSERT/UPDATE matching |
| `--clean` | flag | off | Sanitize column names (lowercase, remove special chars) |
| `--cast` | flag | off | Auto-cast types (dates, booleans, numerics) |
| `--outlier` | float | `0.5` | IQR outlier threshold — set to `0` to disable |
| `--auto-profile` | flag | off | Infer PK automatically from data profile |
| `--report-dir` | path | same directory as CSV | Directory to write the `<csv_stem>_harness.txt` report |

> **Note on `--pk` vs `--constraint`:**
> `--pk` is used for SELECT verification queries after INSERT/UPSERT.
> `--constraint` is the unique key used for UPSERT matching and UPDATE WHERE conditions.
> They can be the same column (e.g. `--pk id --constraint id`) or different
> (e.g. `--pk id --constraint email`).

---

## How It Works

```
CSV File
   │
   ▼
[1] Preprocessing (if flags enabled)
   clean → cast → outlier quarantine → auto-profile PK
   │
   ▼
[2] Data Slicing (CrudTestHarness)
   Deterministically splits rows into INSERT / UPSERT / UPDATE slices
   Mutates non-key columns in each slice to produce verifiable changes
   │
   ▼
[3] Schema Fingerprint BEFORE
   Captures column types + hash of the table before any DML
   │
   ▼
[4] INSERT step
   Sends first 60% of rows → auto_insert()
   Runs verification SELECT → validates row count and values
   │
   ▼
[5] UPSERT step
   Sends 30% overlap (updates existing) + 20% new rows → auto_upsert()
   Runs verification SELECT → validates merged state
   │
   ▼
[6] UPDATE step
   Sends 3 targeted rows → auto_update() WHERE constraint = ?
   Runs verification SELECT → validates full cycle state
   │
   ▼
[7] Schema Fingerprint AFTER
   Compares before/after hash → reports schema drift if detected
   │
   ▼
[8] Write <csv_stem>_harness.txt report
```

---

## Data Slicing

Given a source CSV with N rows, the harness slices it as follows:

```
Row index:  0%        30%       60%       80%      100%
            ├─────────┼─────────┼─────────┼─────────┤
INSERT      [═════════════════════]
UPSERT                [══════════════════]
              overlap─┘          └─new rows
UPDATE      [idx=0, idx=40%, idx=70%]  ← 3 targeted rows
```

| Slice | Rows | Purpose |
|-------|------|---------|
| INSERT | 0 → 60% | Initial load — establishes baseline data in the table |
| UPSERT overlap | 30% → 60% | Rows already inserted — tests UPDATE path of upsert |
| UPSERT new | 60% → 80% | Brand new rows — tests INSERT path of upsert |
| UPDATE | rows at 0%, 40%, 70% | Targeted rows guaranteed to exist — tests UPDATE |

All non-key (mutable) columns are mutated per slice so the harness can verify
the database reflects the correct values after each step:

| Type | Mutation |
|------|----------|
| int / float | `+1` / `+1.0` |
| string | append `⟐m` marker |
| bool | flipped |
| datetime | `+1 day` |
| null | unchanged |

> The source CSV must have **at least 5 rows** for slicing to work.

---

## Preprocessing Pipeline

When the corresponding flags are passed, preprocessing runs in this fixed order
before slicing:

```
1. --clean        sanitize_dataframe_columns()
                  Lowercases column names, removes spaces and special characters.
                  Also remaps --pk and --constraint names through the same mapping.

2. --cast         auto_cast()
                  Infers and converts string columns to dates, booleans, integers,
                  and floats using pattern matching.

3. --outlier      replace_outliers_with_zero_safe()  (threshold default=0.5)
                  IQR-based outlier detection on numeric columns.
                  Replaces extreme values with zero to prevent constraint violations.

4. --auto-profile profile_dataframe() + get_pk()
                  Profiles the DataFrame to infer the primary key automatically.
                  Only activates if --pk is not provided (falls back to "id").
```

---

## Validation & Root Cause Diagnostics

After each DML step, the harness runs a verification SELECT and compares the
database state against the expected in-memory state. Failures are matched against
known error patterns and mapped to a root cause with a fix recommendation.

| Root Cause | Trigger Pattern | Recommended Fix |
|------------|----------------|-----------------|
| VARCHAR overflow | `value too long for varying(N)` | Truncate data or widen the column |
| Oracle VARCHAR2 overflow | `ORA-12899` | ALTER column to VARCHAR2(actual_len) |
| NOT NULL violation | `cannot be null`, `not-null constraint` | Provide defaults or drop null rows |
| Duplicate key | `duplicate key`, `violates unique` | Deduplicate source or use UPSERT mode |
| Foreign key violation | `foreign key constraint` | Insert parent records first |
| Data truncation | `string truncation`, `data truncat` | Widen column or trim source data |
| Numeric overflow | `out of range`, `arithmetic overflow` | Use BIGINT or DOUBLE PRECISION |
| Type casting failure | `cannot cast`, `conversion failed` | Clean non-numeric chars from numeric cols |
| Column mismatch | `no such column`, `unknown column` | Enable `--clean` or check column names |
| Table not found | `relation does not exist` | Create table first or run `sqlpen load` |

Each CRUD operation attempts a **bulk insert first**, then falls back to
**row-by-row execution** on failure, collecting per-row errors up to a tolerance
limit (default: 5 failures before abort).

---

## Report Output

The harness writes `<csv_stem>_harness.txt` to the same directory as the CSV
(or `--report-dir` if specified). The report contains:

```
========================================================================
  SQLPEN HARNESS REPORT
  Table:     users
  Engine:    postgresql
  Generated: 2024-01-15 14:32:01
========================================================================

DATA SLICING SUMMARY
------------------------------------------------------------------------
  Source rows:        1000
  PK columns:         ['id']
  Constraint columns: ['email']
  Mutable columns:    ['name', 'age', 'status']
  INSERT slice:       600 rows (first 60%)
  UPSERT slice:       500 rows (30% overlap + 20% new)
  UPDATE slice:       3 rows (3 targeted rows)

PIPELINE MODE
------------------------------------------------------------------------
  Mode:     FULL (clean → cast → outlier → profile)
  Clean:    ON
  Cast:     ON
  Outlier:  ON (threshold=0.5)
  Profiler: ON

SCHEMA FINGERPRINT (BEFORE)
...

STEP: INSERT
========================================================================
  Status:   PASS
  Rows sent: 600
  Success:  600
  Failed:   0
  Method:   bulk
  Elapsed:  0.412s
  ...

STEP: UPSERT
...

STEP: UPDATE
...

SCHEMA DRIFT
------------------------------------------------------------------------
  No schema changes detected.

========================================================================
  VERDICT: ALL STEPS PASSED
  Total elapsed: 1.203s
========================================================================
```

---

## CLI Examples

### Minimal — table name inferred from filename
```bash
sqlpen harness data.csv
```

### Explicit table and primary key
```bash
sqlpen harness data.csv --table users --pk id
```

### Full preprocessing enabled
```bash
sqlpen harness data.csv --table users --pk id --constraint email --clean --cast --auto-profile
```

### Separate PK and constraint columns
```bash
sqlpen harness telecom.csv --table cdr_raw --pk record_id --constraint msisdn
```

### Composite PK and constraint
```bash
sqlpen harness orders.csv --table orders --pk order_id,line_id --constraint order_id,sku
```

### Explicit database URL
```bash
sqlpen harness data.csv --table users --pk id \
  --url postgresql://user:password@localhost:5432/mydb
```

### MySQL / MariaDB
```bash
sqlpen harness data.csv --table users --pk id \
  --url mysql+pymysql://user:password@localhost/mydb
```

### SQLite (local file)
```bash
sqlpen harness data.csv --table users --pk id \
  --url sqlite:///local.db
```

### Oracle
```bash
sqlpen harness data.csv --table users --pk id \
  --url oracle+cx_oracle://user:password@host:1521/SID
```

### SQL Server
```bash
sqlpen harness data.csv --table users --pk id \
  --url mssql+pyodbc://user:password@host/db?driver=ODBC+Driver+17+for+SQL+Server
```

### Custom report output directory
```bash
sqlpen harness data.csv --table users --pk id --report-dir ./reports
# writes: ./reports/data_harness.txt
```

### Disable outlier detection
```bash
sqlpen harness data.csv --table users --pk id --outlier 0
```

### Auto-profile PK (no --pk needed)
```bash
sqlpen harness data.csv --table users --auto-profile
```

### Full flags — production-grade run
```bash
sqlpen harness telecom_data.csv \
  --table cdr_raw \
  --pk record_id \
  --constraint msisdn \
  --clean \
  --cast \
  --auto-profile \
  --outlier 0.5 \
  --report-dir ./harness_reports \
  --url postgresql://etl_user:secret@db-host:5432/telecom
```

### Using DATABASE_URL from .env (no --url needed)
```bash
# .env contains: DATABASE_URL=postgresql://user:pass@localhost/mydb
sqlpen harness data.csv --table users --pk id --clean --cast
```

### Verbose debug logging
```bash
sqlpen -v harness data.csv --table users --pk id
```

---

## Python API

For programmatic use, call `run_csv_pipeline` directly:

```python
from SqlPen import run_csv_pipeline, get_engine_from_env

engine = get_engine_from_env("postgresql://user:pass@localhost/mydb")

report = run_csv_pipeline(
    csv_path="data.csv",
    engine=engine,
    table="users",
    pk_cols="id",
    constraint_cols="email",
    clean=True,
    cast=True,
    outlier=0.5,
    auto_profiling=True,
    report_dir="./reports",
)

print(report.summary())
print(f"All passed: {report.all_passed}")
print(f"Total elapsed: {report.total_elapsed_s}s")

# Inspect individual steps
for step in report.steps:
    print(f"{step.step}: success={step.crud_result.success}, failed={step.crud_result.failed}")
    for diag in step.diagnostics:
        print(f"  [{diag['root_cause']}] {diag['recommendation']}")
```

Or use `PipelineRunner` directly for more control:

```python
import pandas as pd
from SqlPen import PipelineRunner, CrudConfig, get_engine_from_env

engine = get_engine_from_env()
df = pd.read_csv("data.csv")

runner = PipelineRunner(
    engine=engine,
    source_df=df,
    table="users",
    pk_cols=["id"],
    constraint_cols=["email"],
    config=CrudConfig(chunk_size=5000, trace_sql=True),
    clean=True,
    cast=True,
    outlier=0.5,
    auto_profiling=True,
    report_path="./reports/users_harness.txt",
)

# Run full cycle
report = runner.run()

# Or run individual steps
insert_result  = runner.run_insert_only()
upsert_result  = runner.run_upsert_only()
update_result  = runner.run_update_only()
```

---

## Requirements & Constraints

| Requirement | Detail |
|-------------|--------|
| Minimum rows | CSV must have **at least 5 rows** for slicing to work |
| PK uniqueness | Source data must have **unique values** in the PK column(s) |
| Mutable columns | At least **one non-key column** must exist for mutation |
| Table state | The target table should be **empty or not exist** before running — the harness creates it on first INSERT |
| Database URL | Must be set via `--url`, `DATABASE_URL` env var, or `.env` file |
