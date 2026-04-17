# SqlPen Test Suite

Each test module is fully self-contained and accepts a `--url` CLI argument to run
against any supported database dialect. Defaults to `sqlite:///:memory:` when `--url`
is omitted.

---

## Modules

| Module | What it tests |
|--------|--------------|
| `test_df_tosql.py` | `df_tosql` — all write modes, preprocessing, file formats, edge cases |
| `test_dict_tosql.py` | `dict_tosql` — single dict, list of dicts, upsert, update |
| `test_harness.py` | `PipelineRunner` / `run_csv_pipeline` — full cycle, steps, fingerprint, report |
| `test_csvdog.py` | `csvdog` — skip logic, mtime tracking, PK inference, manifest |
| `test_crud.py` | `auto_insert` / `auto_upsert` / `auto_update` — bulk, fallback, CrudConfig |
| `test_peek.py` | `peek.py` — tables, describe, query, has_table, validate_upsert |
| `test_config.py` | `config.py` — engine resolution, ensure_table, load_pipeline_config |

---

## Running Tests

### Default — SQLite in-memory
```bash
pytest tests/
```

### PostgreSQL
```bash
pytest tests/ --url "postgresql://user:pass@localhost:5432/mydb"
```

### MySQL / MariaDB
```bash
pytest tests/ --url "mysql+pymysql://user:pass@localhost/mydb"
```

### SQLite file
```bash
pytest tests/ --url "sqlite:///test.db"
```

### SQL Server
```bash
pytest tests/ --url "mssql+pyodbc://user:pass@host/db?driver=ODBC+Driver+17+for+SQL+Server"
```

### Oracle
```bash
pytest tests/ --url "oracle+cx_oracle://user:pass@host:1521/SID"
```

---

## Running a Single Module

```bash
# Only df_tosql tests against PostgreSQL
pytest tests/test_df_tosql.py --url "postgresql://user:pass@localhost/mydb" -v

# Only harness tests against MySQL
pytest tests/test_harness.py --url "mysql+pymysql://user:pass@localhost/mydb" -v

# Only peek tests against SQLite
pytest tests/test_peek.py -v
```

---

## Running a Single Test Class or Case

```bash
# Run only the upsert tests
pytest tests/test_df_tosql.py::TestUpsert -v

# Run one specific test
pytest tests/test_crud.py::TestAutoInsert::test_insert_chunked --url "postgresql://..." -v
```

---

## Verbose + Log Output

```bash
pytest tests/ --url "postgresql://..." -v -s
```

---

## Requirements

```bash
pip install pytest pandas sqlalchemy
# For PostgreSQL
pip install psycopg2-binary
# For MySQL
pip install pymysql
# For Oracle
pip install cx_Oracle
# For SQL Server
pip install pyodbc
```
