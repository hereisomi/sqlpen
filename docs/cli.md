# SqlPen CLI Reference 🖋️

_Complete command-line reference for SqlPen v0.1.0_
_Source: [github.com/hereisomi/sqlpen](https://github.com/hereisomi/sqlpen)_

SqlPen provides a unified CLI for ETL loading, DML testing, database introspection,
batch job execution, and configuration management. All commands resolve the database
connection from `--url`, the `DATABASE_URL` environment variable, or a `.env` file.

> Related docs: [harness.md](harness.md) · [pipeline.md](pipeline.md) · [peek.md](peek.md) · [Back to README](../README.md)

---

## Table of Contents

- [Global Options](#global-options)
- [Database URL Resolution](#database-url-resolution)
- [Commands Overview](#commands-overview)
- [sqlpen load](#sqlpen-load)
- [sqlpen harness / test](#sqlpen-harness--test)
- [sqlpen run](#sqlpen-run)
- [sqlpen query](#sqlpen-query)
- [sqlpen tables](#sqlpen-tables)
- [sqlpen describe](#sqlpen-describe)
- [sqlpen config](#sqlpen-config)
  - [config init](#config-init)
  - [config show](#config-show)
  - [config set](#config-set)
  - [config add-job](#config-add-job)
  - [config clear-jobs](#config-clear-jobs)

---

## Global Options

These options apply to every command and must be placed **before** the subcommand name.

| Option | Description |
|--------|-------------|
| `-v`, `--verbose` | Enable DEBUG level logging (default: INFO) |
| `--version` | Show SqlPen version and exit |
| `--help` | Show help message and exit |

```bash
# Show version
sqlpen --version

# Enable verbose debug logging for any command
sqlpen -v load data.csv --table users
sqlpen -v harness data.csv --table users --pk id
```

---

## Database URL Resolution

Every command that connects to a database resolves the URL in this order:

```
1. --url argument (highest priority)
2. DATABASE_URL environment variable
3. DATABASE_URL key in .env file (CWD or project root)
```

Supported URL formats:

| Dialect | URL Format |
|---------|-----------|
| PostgreSQL | `postgresql://user:pass@host:5432/dbname` |
| MySQL / MariaDB | `mysql+pymysql://user:pass@host/dbname` |
| SQLite | `sqlite:///path/to/file.db` |
| Oracle | `oracle+cx_oracle://user:pass@host:1521/SID` |
| SQL Server | `mssql+pyodbc://user:pass@host/db?driver=ODBC+Driver+17+for+SQL+Server` |

```bash
# .env file example
DATABASE_URL=postgresql://etl_user:secret@localhost:5432/mydb
```

---

## Commands Overview

| Command | Purpose |
|---------|---------|
| `sqlpen load <file>` | Load CSV/Parquet/JSON/Excel into a database table |
| `sqlpen harness <csv>` | Run INSERT → UPSERT → UPDATE diagnostic cycle |
| `sqlpen test <csv>` | Alias for `sqlpen harness` |
| `sqlpen run` | Execute all batch jobs defined in `config.yml` |
| `sqlpen query "<sql>"` | Run a SQL query and display or export results |
| `sqlpen tables` | List all tables in the database |
| `sqlpen describe <table>` | Show column schema for a table |
| `sqlpen config` | Manage `config.yml` settings |

---

## sqlpen load

Load a file into a database table. Supports CSV, Parquet, JSON, Excel, remote URLs, and S3 paths.

```bash
sqlpen load <SOURCE> [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `SOURCE` | path/url | required | Local file path or remote URL (http/https/s3) |
| `--table` | string | inferred from filename | Target table name |
| `--url` | string | env / `.env` | Database connection URL |
| `--if-exist` | choice | `insert` | Write mode: `insert`, `replace`, `upsert`, `update` |
| `--schema` | string | None | Database schema/namespace |
| `--chunk` | int | `1000` | Batch size for bulk operations |
| `--constraint` | string | None | Comma-separated unique constraint columns (required for upsert) |
| `--no-clean` | flag | off | Skip column name sanitization |
| `--no-cast` | flag | off | Skip automatic type casting |
| `--outlier` | float | `0.5` | IQR outlier threshold — set to `0` to disable |
| `--schema-name` | path | `""` | File path to dump schema metadata as JSON |

### Write Modes (`--if-exist`)

| Mode | Behaviour |
|------|-----------|
| `insert` | Append rows. Auto-creates table if it does not exist. |
| `replace` | Drop the table, recreate it, then insert all rows. |
| `upsert` | Insert new rows, update existing rows matched by `--constraint`. |
| `update` | Update existing rows only. Table must already exist. |

### Examples

```bash
# Basic insert — table name inferred as "users" from filename
sqlpen load users.csv

# Explicit table name
sqlpen load users.csv --table users

# Upsert with constraint column
sqlpen load users.csv --table users --if-exist upsert --constraint id

# Replace (drop + recreate + insert)
sqlpen load users.csv --table users --if-exist replace

# Load Parquet file
sqlpen load data.parquet --table events --if-exist upsert --constraint event_id

# Load Excel file
sqlpen load report.xlsx --table monthly_report

# Load JSON file
sqlpen load records.json --table raw_records

# Load from remote URL
sqlpen load https://example.com/data.csv --table remote_data

# Load from S3
sqlpen load s3://my-bucket/data/users.csv --table users

# Custom chunk size for large files
sqlpen load big_file.csv --table logs --chunk 5000

# Skip cleaning and casting
sqlpen load data.csv --table raw --no-clean --no-cast

# Dump schema metadata to JSON
sqlpen load data.csv --table users --schema-name ./schema/users.json

# Target a specific database schema (e.g. Postgres)
sqlpen load data.csv --table users --schema public

# Explicit database URL
sqlpen load data.csv --table users \
  --url postgresql://user:pass@localhost:5432/mydb

# Composite upsert constraint
sqlpen load orders.csv --table orders --if-exist upsert --constraint order_id,line_id

# Disable outlier detection
sqlpen load data.csv --table users --outlier 0
```

---

## sqlpen harness / test

Run the diagnostic DML test harness against a CSV file. Executes a deterministic
INSERT → UPSERT → UPDATE cycle, validates results at each step, and writes a
`<csv_stem>_harness.txt` report with root cause diagnostics.

`sqlpen harness` and `sqlpen test` are identical aliases.

```bash
sqlpen harness <SOURCE> [OPTIONS]
sqlpen test    <SOURCE> [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `SOURCE` | path | required | Path to a local CSV file |
| `--table` | string | inferred from filename | Target table name |
| `--url` | string | env / `.env` | Database connection URL |
| `--pk` | string | `"id"` | Comma-separated primary key column(s) |
| `--constraint` | string | `"id"` | Comma-separated constraint column(s) for UPSERT/UPDATE |
| `--clean` | flag | off | Sanitize column names before testing |
| `--cast` | flag | off | Auto-cast types (dates, booleans, numerics) |
| `--outlier` | float | `0.5` | IQR outlier threshold — set to `0` to disable |
| `--auto-profile` | flag | off | Infer PK automatically from data profile |
| `--report-dir` | path | same dir as CSV | Directory to write the harness report |

> See [harness.md](harness.md) for the full harness documentation.

### Examples

```bash
# Minimal — table inferred from filename, pk defaults to "id"
sqlpen harness data.csv

# Explicit table and PK
sqlpen harness data.csv --table users --pk id

# Full preprocessing enabled
sqlpen harness data.csv --table users --pk id --constraint email \
  --clean --cast --auto-profile

# Separate PK and constraint columns
sqlpen harness telecom.csv --table cdr_raw --pk record_id --constraint msisdn

# Composite keys
sqlpen harness orders.csv --table orders --pk order_id,line_id --constraint order_id,sku

# Custom report output directory
sqlpen harness data.csv --table users --pk id --report-dir ./reports

# Disable outlier detection
sqlpen harness data.csv --table users --pk id --outlier 0

# Auto-profile PK (no --pk needed)
sqlpen harness data.csv --table users --auto-profile

# Using test alias
sqlpen test data.csv --table users --pk id --clean --cast

# Full production-grade run
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

# Verbose debug output
sqlpen -v harness data.csv --table users --pk id
```

---

## sqlpen run

Execute all batch load jobs defined in the `jobs:` section of `config.yml` sequentially.

```bash
sqlpen run [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config` | path | auto-discovered | Path to `config.yml` (searches CWD then project root) |

### config.yml jobs format

```yaml
database_url: postgresql://user:pass@localhost/mydb   # default for all jobs

jobs:
  - source: data/users.csv
    table: users
    if_exist: upsert
    constraint: id
    chunk: 5000
    clean: true
    cast: true
    outlier: 0.5

  - source: data/orders.parquet
    table: orders
    if_exist: insert
    database_url: sqlite:///orders.db   # per-job URL override
```

### Job fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `source` | yes | — | File path (CSV/Parquet/JSON/Excel) |
| `table` | yes | — | Target table name |
| `if_exist` | no | `insert` | Write mode: `insert`, `replace`, `upsert`, `update` |
| `constraint` | no | `""` | Unique constraint columns for upsert |
| `chunk` | no | `1000` | Batch size |
| `clean` | no | `true` | Sanitize column names |
| `cast` | no | `true` | Auto-cast types |
| `outlier` | no | `0.5` | IQR outlier threshold |
| `schema_name` | no | `""` | Path to dump schema JSON |
| `database_url` | no | top-level `database_url` | Per-job URL override |

### Examples

```bash
# Run all jobs using auto-discovered config.yml
sqlpen run

# Run jobs from a specific config file
sqlpen run --config ./configs/production.yml

# Verbose output
sqlpen -v run
```

---

## sqlpen query

Execute a SQL query and display results in the terminal or export to a file.

```bash
sqlpen query "<SQL>" [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `SQL` | string | required | SQL query string (wrap in quotes) |
| `--url` | string | env / `.env` | Database connection URL |
| `--limit` | int | `20` | Max rows to display in terminal |
| `-o`, `--output` | path | None | Export results to `.csv` or `.json` file |

### Examples

```bash
# Simple SELECT
sqlpen query "SELECT * FROM users LIMIT 10"

# With WHERE clause
sqlpen query "SELECT id, name, email FROM users WHERE active = 1"

# Aggregate query
sqlpen query "SELECT status, COUNT(*) as cnt FROM orders GROUP BY status"

# Display more rows
sqlpen query "SELECT * FROM events" --limit 100

# Export to CSV
sqlpen query "SELECT * FROM users" -o users_export.csv

# Export to JSON
sqlpen query "SELECT * FROM orders WHERE date > '2024-01-01'" -o orders.json

# Cross-table join
sqlpen query "SELECT u.name, COUNT(o.id) as orders FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name"

# With explicit database URL
sqlpen query "SELECT COUNT(*) FROM logs" --url sqlite:///app.db

# DDL inspection
sqlpen query "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
```

---

## sqlpen tables

List all tables in the connected database.

```bash
sqlpen tables [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--url` | string | env / `.env` | Database connection URL |
| `--schema` | string | None | Filter by database schema/namespace |

### Examples

```bash
# List all tables
sqlpen tables

# List tables in a specific schema (PostgreSQL)
sqlpen tables --schema public

# List tables in a specific schema (SQL Server)
sqlpen tables --schema dbo

# With explicit URL
sqlpen tables --url postgresql://user:pass@localhost/mydb

# With schema filter
sqlpen tables --schema analytics --url postgresql://user:pass@localhost/mydb
```

---

## sqlpen describe

Show column metadata for a table. Use `--full` to include primary keys and unique constraints.

```bash
sqlpen describe <TABLE> [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `TABLE` | string | required | Table name to describe |
| `--url` | string | env / `.env` | Database connection URL |
| `--schema` | string | None | Database schema/namespace |
| `--full` | flag | off | Include PKs, unique constraints, and identity columns |

### Output

Without `--full` — returns a DataFrame-style table:
```
      name          type  nullable  default  autoincrement
        id       INTEGER     False     None           True
      name  VARCHAR(255)      True     None          False
     email  VARCHAR(255)      True     None          False
    active       INTEGER      True        1          False
created_at          TEXT      True     None          False
```

With `--full` — returns structured metadata:
```
Table: users
Schema: public

Columns:
  - id: INTEGER (nullable=False)
  - name: VARCHAR(255) (nullable=True)
  - email: VARCHAR(255) (nullable=True)

Primary Keys: id
Unique Constraints:
  - email
```

### Examples

```bash
# Basic column info
sqlpen describe users

# Full metadata with PKs and constraints
sqlpen describe users --full

# Describe table in a specific schema
sqlpen describe users --schema public

# With explicit URL
sqlpen describe orders --full --url postgresql://user:pass@localhost/mydb

# SQL Server with schema
sqlpen describe dbo.customers --schema dbo --full
```

---

## sqlpen config

Manage `config.yml` settings. A subcommand group with 5 commands.

```bash
sqlpen config <SUBCOMMAND> [OPTIONS]
```

---

### config init

Create a new `config.yml` with sensible defaults in the current directory.

```bash
sqlpen config init [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config` | path | `./config.yml` | Path to write the config file |
| `--force` | flag | off | Overwrite existing `config.yml` |

```bash
# Create config.yml in CWD
sqlpen config init

# Overwrite existing
sqlpen config init --force

# Write to a specific path
sqlpen config init --config ./configs/prod.yml
```

Generated `config.yml` structure:

```yaml
logging:
  enabled: true
  dir: log
  max_repr_len: 2000
  bucket_minutes: 10

pipeline:
  outlier_pct: 0.5
  casting: true
  cleaner: true
  profiler: true
  trace_sql: false
  schema_save_path: schema
  chunk_size: 10000

schema_corrector:
  on_error: coerce
  failure_threshold: 3.0
  validate_fk: false
  add_missing_cols: false

casting:
  use_transform: true
  infer_threshold: 0.9
  nan_threshold: 0.30
  max_null_increase: 0.1
  max_sample_size: 1000
  validate_conversions: true
  parallel: false
  chunk_size: 50000

jobs: []
```

---

### config show

Display the full `config.yml` or a specific key using dot-notation.

```bash
sqlpen config show [KEY] [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `KEY` | string | None | Dot-notation key to inspect (optional) |
| `--config` | path | auto-discovered | Path to `config.yml` |

```bash
# Show entire config
sqlpen config show

# Show a specific section
sqlpen config show pipeline

# Show a specific key
sqlpen config show pipeline.chunk_size

# Show schema_corrector section
sqlpen config show schema_corrector

# Show jobs list
sqlpen config show jobs

# From a specific config file
sqlpen config show --config ./configs/prod.yml pipeline.trace_sql
```

---

### config set

Set a `config.yml` value using dot-notation. Auto-converts value types
(int, float, bool, string).

```bash
sqlpen config set <KEY> <VALUE> [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `KEY` | string | required | Dot-notation key path |
| `VALUE` | string | required | New value (auto-converted to int/float/bool/str) |
| `--config` | path | auto-discovered | Path to `config.yml` |

```bash
# Set chunk size
sqlpen config set pipeline.chunk_size 5000

# Enable SQL tracing
sqlpen config set pipeline.trace_sql true

# Disable casting
sqlpen config set pipeline.casting false

# Set outlier threshold
sqlpen config set pipeline.outlier_pct 1.0

# Set failure threshold
sqlpen config set schema_corrector.failure_threshold 5.0

# Allow adding missing columns
sqlpen config set schema_corrector.add_missing_cols true

# Set default database URL
sqlpen config set database_url postgresql://user:pass@localhost/mydb

# Set logging directory
sqlpen config set logging.dir ./logs

# In a specific config file
sqlpen config set pipeline.chunk_size 2000 --config ./configs/prod.yml
```

---

### config add-job

Append a new load job to the `jobs:` list in `config.yml`.

```bash
sqlpen config add-job --source <FILE> [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--source` | path | required | Source file path |
| `--table` | string | inferred from filename | Target table name |
| `--url` | string | None | Per-job database URL override |
| `--if-exist` | choice | `insert` | Write mode: `insert`, `replace`, `upsert`, `update` |
| `--constraint` | string | None | Unique constraint columns |
| `--config` | path | auto-discovered | Path to `config.yml` |

```bash
# Add a simple insert job
sqlpen config add-job --source data/users.csv --table users

# Add an upsert job with constraint
sqlpen config add-job --source data/users.csv --table users \
  --if-exist upsert --constraint id

# Add a replace job
sqlpen config add-job --source data/products.csv --table products \
  --if-exist replace

# Add a job with a per-job database URL
sqlpen config add-job --source data/logs.csv --table logs \
  --url sqlite:///logs.db

# Add a job with composite constraint
sqlpen config add-job --source data/orders.csv --table orders \
  --if-exist upsert --constraint order_id,line_id

# Add to a specific config file
sqlpen config add-job --source data/users.csv --table users \
  --config ./configs/prod.yml
```

---

### config clear-jobs

Remove all jobs from the `jobs:` list in `config.yml`. Prompts for confirmation.

```bash
sqlpen config clear-jobs [OPTIONS]
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--config` | path | auto-discovered | Path to `config.yml` |
| `--yes` | flag | off | Skip confirmation prompt |

```bash
# Clear all jobs (prompts for confirmation)
sqlpen config clear-jobs

# Skip confirmation
sqlpen config clear-jobs --yes

# Clear jobs from a specific config file
sqlpen config clear-jobs --config ./configs/prod.yml --yes
```

---

## Full Workflow Example

A complete end-to-end workflow from setup to batch execution:

```bash
# 1. Initialize config
sqlpen config init

# 2. Set database URL
sqlpen config set database_url postgresql://user:pass@localhost/mydb

# 3. Inspect the database
sqlpen tables
sqlpen describe users --full

# 4. Test a CSV before loading (diagnostic harness)
sqlpen harness data/users.csv --table users --pk id --constraint email \
  --clean --cast --auto-profile --report-dir ./reports

# 5. Load the file
sqlpen load data/users.csv --table users --if-exist upsert --constraint id

# 6. Verify the load
sqlpen query "SELECT COUNT(*) FROM users"
sqlpen query "SELECT * FROM users LIMIT 5" -o sample.csv

# 7. Add more jobs to config and run batch
sqlpen config add-job --source data/orders.csv --table orders --if-exist upsert --constraint order_id
sqlpen config add-job --source data/products.csv --table products --if-exist replace
sqlpen run

# 8. Export results
sqlpen query "SELECT u.name, COUNT(o.id) as total_orders FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name" \
  -o reports/user_orders.csv
```
