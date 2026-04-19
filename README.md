# SqlSql: Modernized ETL & DML Pipeline

A professional-grade, programmatic ETL framework designed for reliable data ingestion into SQL databases, with specialized hardening for Oracle, PostgreSQL, and SQLite.

## Features

- **Zero-Config ETL**: Load CSV, Parquet, JSON, and Excel directly into database tables.
- [x] Oracle Hardened: Specialized handling for identifier casing and precision issues (`binary_precision`).
- [x] Smart Alignment: Decouples schema analysis from execution; assessment of data compatibility before writing.
- [x] DML Test Harness: Automated INSERT -> UPSERT -> UPDATE benchmarking with root-cause failure analysis.
- [x] Guided Wizard: Cross-platform interactive terminal setup for database connections.
- [x] Batch Processing: Orchestrate multiple ETL jobs via YAML configuration.

## Pipeline Workflow

SqlSql follows a robust, multi-stage pipeline to ensure data integrity and cross-dialect compatibility:

```mermaid
flowchart TD
    Start([User calls pipeline.run]) --> Setup[Initialize AlignmentPolicies]
    Setup --> Empty{Is DF Empty?}
    Empty -- Yes & Mode=Insert --> ShortCircuit([Return 0 rows])
    Empty -- No --> Inspect{Table Exists in DB?}

    %% Path A: Table Missing
    Inspect -- No --> CreateCheck{apply_ddl=True?}
    CreateCheck -- No --> Error[Raise ValueError]
    CreateCheck -- Yes --> GenDDL[utils.ddl_create.df_ddl]
    GenDDL --> ExecDDL[conn.execute CREATE TABLE]
    ExecDDL --> Reflect

    %% Path B: Table Exists
    Inspect -- Yes --> Reflect[reflect_table_spec]
    
    Reflect --> Sync[_synchronize_df_casing]
    Sync --> Analyze[analyze schema drift]
    
    Analyze --> Drifts{Drift Detected?}
    Drifts -- Yes --> DDLPlan{apply_ddl=True?}
    DDLPlan -- Yes --> apply_ddl_plan[apply_ddl_plan ALTER TABLE]
    DDLPlan -- No --> Error2[Raise RuntimeError]
    
    Drifts -- No --> Coerce[coerce_dataframe cleaning]
    apply_ddl_plan --> Coerce
    
    Coerce --> ModeSelect{Mode?}
    ModeSelect -- insert --> Insert[sql_generator.insert]
    ModeSelect -- upsert --> Upsert[sql_generator.upsert]
    ModeSelect -- update --> Update[sql_generator.update]
    ModeSelect -- update_track --> Track[sql_generator.update_track]
    
    Insert --> End([Return Result Dict])
    Upsert --> End
    Update --> End
    Track --> End
```

## Installation

```bash
pip install pandas sqlalchemy click pyyaml
# Install dialect drivers as needed, e.g.:
pip install oracledb psycopg2-binary pymysql pyodbc
```

## CLI Usage

### 1. Interactive Setup (Wizard)
Launch the guided menu to configure your database connection and save it to `.env`.
```bash
python cli.py setup
# OR simply launch the wrapper
./sqlsql.bat
```

### 2. Loading Data (Load)
Load a single file into a table. The pipeline automatically analyzes schema compatibility and can apply DDL changes.
```bash
# Basic insert (appends to table)
python cli.py load data/users.csv --table users --url sqlite:///test.db

# Upsert (Insert or Update) - Requires PK columns
python cli.py load data/orders.parquet --table orders --ops upsert --pk order_id --apply-ddl

# Overwrite (Replace) existing table
python cli.py load data/legacy.json --table legacy_data --ops replace
```

### 3. Running Batch Jobs (Run)
Execute multiple jobs defined in a `config.yml` file.
```bash
python cli.py run --config config.yml
```

### 4. DML Benchmarking (Harness)
Validate a database's ability to handle your data through a full CRUD cycle.
```bash
python cli.py test data/benchmark.csv --table perf_test --pk id --url oracle+oracledb://...
```

---

## Programmatic Usage

SqlSql is designed to be embedded directly into your Python scripts.

```python
from sqlalchemy import create_engine
from pipeline import run
from aligner import AlignmentPolicies

engine = create_engine("oracle+oracledb://user:pass@localhost/XEPDB1")

# Define custom policies (e.g., skip outliers, force numeric coercion)
policies = AlignmentPolicies()
policies.outliers.enabled = True
policies.strict_mode = False

# Execute the pipeline
results = run(
    engine=engine,
    df=my_dataframe,
    table="employees",
    schema="HR",
    mode="upsert",
    constrain=["emp_id"],
    policies=policies,
    apply_ddl=True
)

print(f"Rows processed: {results['result']}")
```

---

## Configuration Example (`config.yml`)

```yaml
database_url: "postgresql://postgres:pass@localhost:5432/etl_db"

jobs:
  - source: "csv/quota.csv"
    table: "quota_table"
    mode: "upsert"
    pk: "COST_ID"
    apply_ddl: true
    
  - source: "https://example.com/data.parquet"
    table: "remote_sync"
    mode: "insert"
    chunk: 5000
```

## License
MIT
