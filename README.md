# SqlPen 🖋️

_A Zero-Config ETL Engine & Diagnostic DML Test Harness for Pandas → SQL_

![PyPI](https://img.shields.io/badge/status-alpha-yellow) ![license](https://img.shields.io/badge/license-MIT-blue)

**SqlPen** is designed to seamlessly move datasets from CSV/Parquet into relational databases, with a specific focus on **deterministic validation** and **root-cause diagnostics** for Database writes (Insert/Upsert/Update). 

Built originally to harden telecom ingestion pipelines, it identifies why data fails to load (e.g. constraints, truncation, type inference) and generates actionable error reports.

---

## Table of Contents
- [Features](#features)
- [Installation](#installation)
- [Quick Start: Interactive Launcher](#quick-start-interactive-launcher)
- [CLI Reference](#cli-reference)
- [Python API](#python-api)
- [Diagnostics & The Harness Report](#diagnostics--the-harness-report)
- [Configuration](#configuration)

---

## Features

- **Diagnostic DML Harness (`sqlpen test` / `sqlpen harness`)**  
  Runs a deterministically sliced `INSERT` → `UPSERT` → `UPDATE` validation cycle on raw CSVs. Generates a comprehensive `<csv_name>_harness.txt` report mapping SQL exceptions to actionable **Root Cause** fixes (e.g., VARCHAR overflow, NOT NULL violations, PK mismatches).
- **Universal Pipelines (`df_tosql`)**  
  Auto-sanitizes column names, casts types, and quarantines outliers before dynamically adapting schemas (`CREATE` or `ALTER TABLE`).
- **Interactive Windows Launcher**  
  A fully guided UI via `sqlpen.bat` with a built-in Connection Wizard and `.env` management.
- **Dialect-native CRUD**  
  Optimized bulk execution and per-row fallback diagnostics for **PostgreSQL · MySQL/MariaDB · SQLite · Oracle · SQL Server**.
- **Introspection Layer (`peek.py`)**  
  One-liner database inspection: `tables()`, `describe_full()`, and `query()`.
- **Batch Processing (`csvdog`)**  
  Run complex predefined ETL jobs sequentially using a smart `config.yml` configuration system.

---

## Installation

```bash
# Clone the repository
$ git clone https://github.com/yourname/sqlpen.git && cd sqlpen

# Install globally in editable mode
$ pip install -e .
```
_Requirements: Python ≥ 3.8, Pandas ≥ 1.4, SQLAlchemy ≥ 1.4._

---

## Quick Start: Interactive Launcher

For Windows users, the absolute fastest way to use SqlPen is the interactive batch script:

```bash
# Run the wizard from any directory
$ sqlpen.bat
```
- **First launch:** It prompts you to build your database connection and saves it securely to a local `.env` file.
- **Main Menu:** Guides you through loading files, testing datasets, running SQL queries, and modifying your configuration.

---

## CLI Reference

SqlPen provides a powerful CLI that works anywhere Python runs. Note: all commands accept a `--url` argument, or automatically read `DATABASE_URL` from your `.env` file.

| Command | Purpose |
|---------|---------|
| `sqlpen load <file> --table <name>` | Loads CSV/Parquet/JSON/Excel. Options: `--if-exist` (insert/upsert/replace), `--constraint`, `--no-clean` |
| `sqlpen harness <csv> --table <name>` | Runs the benchmark DML cycle and outputs the diagnostic `.txt` report. (Alias: `sqlpen test`) |
| `sqlpen config` | Manage `config.yml`. Uses dot-notation (`sqlpen config set pipeline.chunk_size 5000`). |
| `sqlpen run` | Executes all batch load jobs defined in your `config.yml` sequentially. |
| `sqlpen query "<sql>"` | Runs arbitrary SQL. Use `-o results.csv` to export output. |
| `sqlpen tables` | Lists all tables in the connected database/schema. |
| `sqlpen describe <table_name>` | Shows column metadata. Add `--full` for PK and Constraint details. |

---

## Python API

### 1. Robust ETL Loading
Clean, profile, quarantine, and Upsert a DataFrame into a database effortlessly:

```python
import pandas as pd
from SqlPen import df_tosql, get_engine_from_env

engine = get_engine_from_env("sqlite:///example.db")
df = pd.read_csv("telecom_data.csv")

report = df_tosql(
    df=df,
    table="telecom_events",
    engine=engine,
    if_exist="upsert",        # replace | insert | upsert | update
    constraint_cols="msisdn", # Used for the UPSERT constraint matching
    clean=True, cast=True, outlier=0.5
)
print(f"Success: {report.success}, Failed: {report.failed}")
```

### 2. Introspection (Peek)
The `peek.py` module exposes hyper-fast database inspection.

```python
import peek as pk

# List tables
print(pk.tables("sqlite:///example.db"))

# View full schema definition including limits and constraints
schema = pk.describe_full("users", url="sqlite:///example.db")

# Query directly to a DataFrame
df = pk.query("SELECT * FROM users LIMIT 10", url="sqlite:///example.db")
```

---

## Diagnostics & The Harness Report

When you run `sqlpen harness telecom_data.csv --table cdr_raw`, the engine executes targeted slices of your data (Insert 30%, Upsert overlap, Update row match). 

It generates `telecom_data_harness.txt` alongside your CSV containing:
1. **Pipeline State:** Shows if column cleaning, type casting, or outlier quarantine altered the data before testing.
2. **Schema Fingerprint:** Detects schema drift before and after the DML operations.
3. **Raw SQL Logs:** Shows the exact Dialect-specific statements (`INSERT INTO... ON CONFLICT`, etc).
4. **Root Cause Analysis:** Maps row-level database errors to human-readable fixes.
   * _Example:_ Database throws `ORA-12899` -> Harness diagnosis: **"VARCHAR overflow. Truncate data or widen column."**

---

## Configuration

SqlPen uses a `config.yml` file to manage logging limits, schema casting constraints, and automated job runs.

Initialize the defaults:
```bash
$ sqlpen config init
```

Modify settings via CLI:
```bash
$ sqlpen config set pipeline.trace_sql true
$ sqlpen config add-job --source data.csv --table users --if-exist upsert
```

---

## Contributing
1. Fork & clone the repo.
2. Install testing dependencies: `pip install -e .[dev]`.
3. Run tests via `pytest` and linting via `pre-commit`.
4. Follow Conventional Commits format for PRs.

## License
MIT License
