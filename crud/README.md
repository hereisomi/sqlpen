# SqlPen CRUD Toolkit

Robust, dialect-aware bulk **insert**, **upsert**, and **update** helpers built on SQLAlchemy & pandas.

* Handles millions of rows with chunked bulk DML + automatic row-level fallback.
* Online schema-evolution: optional `ALTER TABLE ADD COLUMN` when new DataFrame columns appear.
* Aggressive data normalisation: NaN/NaT cleaning, type coercion, length & precision enforcement.
* Supports PostgreSQL, Oracle, MySQL/MariaDB, Microsoft SQL Server, SQLite.
* Generates **schema fingerprints** + human diff reports to detect hidden drift.
* Fully configurable via a single `CrudConfig` dataclass.

---
## Installation
```bash
pip install sqlalchemy pandas testcontainers  # plus DB drivers e.g. psycopg2-binary
```

---
## Quick start
```python
import pandas as pd
from sqlalchemy import create_engine
from crud import auto_upsert, CrudConfig

engine = create_engine("postgresql+psycopg2://user:pass@host/db")

data = pd.read_csv("customers.csv")

result = auto_upsert(
    engine,
    data,
    table="customers",
    constrain=["customer_id"],
    config=CrudConfig(chunk_size=5000, byte_semantics=True),
)
print(result)
```

---
## Configuration (`CrudConfig`)
| Field | Default | Description |
|-------|---------|-------------|
| `chunk_size` | `10_000` | Rows per bulk execute. |
| `tolerance` | `5` | Max row failures before aborting fallback. |
| `strict` | `True` | Raise on validation/coercion problems instead of logging. |
| `add_missing_cols` | `True` | Auto-add DataFrame columns via `ALTER TABLE ADD COLUMN`. |
| `drop_extra_cols` | `True` | Silently drop DataFrame cols not in target (set `False` to raise). |
| `byte_semantics` | `False` | Measure `VARCHAR` length in **bytes** (UTF-8) vs characters. |
| `trace_sql` | `False` | Dump generated SQL under `./sql_trace/` for debugging. |
| `on_error` | `"coerce"` | `"coerce"` = replace bad values with NULL; `"raise"` = abort. |
| `failure_threshold` | `0.03` | % of failures tolerated in coercion before abort. |
| `enable_fingerprint` | `True` | Attach schema fingerprint to `CrudResult.diagnostics`. |

---
## Schema fingerprinting
A **fingerprint** is a 16-char SHA-256 prefix of the table schema snapshot (column name, type, nullability, default, identity/computed flags).  Callers can:
```python
from crud.fingerprint import build_fingerprint, diff_fingerprints
insp = sa.inspect(engine)
old_fp = build_fingerprint(insp, "my_table")
# ... run migration ...
new_fp = build_fingerprint(insp, "my_table")
print(diff_fingerprints(old_fp, new_fp).summary())
```
Collision probability for 16 hex chars (64 bits) is astronomically low for typical deployments (<10⁻¹³ with 10⁶ tables).  Full 64-char hash is available via `SchemaFingerprint.hash`.

---
## CRUD operations
### Insert
```python
from crud import auto_insert

auto_insert(
    engine,
    [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
    ],
    table="people",
    config=CrudConfig(chunk_size=1000)
)
```

### Upsert (insert or update on key)
```python
from crud import auto_upsert

auto_upsert(
    engine,
    df,                       # pandas DataFrame accepted
    table="people",
    constrain=["id"],        # unique/primary keys
    config=CrudConfig(chunk_size=5000)
)
```

### Update with `where` clause
`auto_update` lets you specify per-row expressions or bind-style templates. Supported `where` formats:
- Tuple `(column, operator, value)` where `value` may be `?` to pull from each row.
- SQL-like string e.g. `"status = ?"`.
- Combine multiple conditions via `expression` string e.g. `"1 AND (2 OR 3)"` (indices are 1-based order of `where`).

```python
from crud import auto_update

# Example: deactivate customers that appear in df
cond = [
    ("customer_id", "=", "?"),   # positional 1
]

result = auto_update(
    engine,
    df,                   # rows containing customer_id column
    table="customers",
    where=cond,
    config=CrudConfig(strict=False),
)
print(result.success, "rows updated")
```

---
## Testing
1. **Local SQLite**:
   ```bash
   pytest crud/tests -q
   ```
2. **Full matrix (Postgres, MySQL, Oracle, MSSQL)**:
   ```bash
   export RUN_DIALECT_TESTS=1
   pytest crud/tests --docker -q
   ```
   Requires Docker & [testcontainers-python](https://github.com/testcontainers/testcontainers-python).

---
## Contributing
PRs welcome!  See `crud/tests/bench_chunk.py` for performance harness and open **TODOs** in `refactor.md`.
