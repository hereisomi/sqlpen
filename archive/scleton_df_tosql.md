# df_tosql Implementation Skeleton

This document outlines a complete implementation plan for `df_tosql()` in `ops/df_tosql.py`, using existing modules plus minimal new helpers.

## 1) Functional Goal
`df_tosql()` should accept a DataFrame or a file path and persist data into a SQL table with optional:
- table creation
- schema evolution (add columns / widen types)
- cleaning, casting, profiling
- insert / update / upsert
- metadata persistence in a JSON config

## 2) Expected Inputs (Current Signature)
```
(df, table, engine=None, if_exist="insert", dtype=None, schema=None,
 chunk=1000, table_constraints=None, where=None, expression="AND",
 add_new_column=True, clean=True, cast=True, auto_profiling=False,
 outlier=0.5, table_json="abc.json")
```

## 3) Required Existing Modules
- `utils/casting.py` → main smart casting
- `utils/trycast.py` → optional fallback cast
- `utils/profiler.py` → profiling + PK inference
- `utils/ddl.py` → DDL + schema JSON
- `aligner/*` → schema alignment / add columns / alter columns
- `sql_generator/router.py` → insert/update/upsert

## 4) Missing Utilities (Must Be Added)
1. **Cleaner** (`utils/cleaner.py` or `utils/clean.py`)
   - Should remove outliers / normalize strings / trim whitespace
   - Can wrap `aligner/outliers.py` or `utils/trycast.py` outlier helpers

2. **Engine loader from `.env`**
   - If `engine is None`, load SQLAlchemy URL from `.env`

3. **JSON config manager** (`table_json`)
   - Load + persist table schema + argument overrides

## 5) Implementation Flow (Step-by-Step)

### Step A — Normalize inputs
1. If `df` is a path or URL, read to DataFrame:
   - CSV / Excel / JSON / Parquet
2. Validate `table` name non-empty.
3. Resolve SQLAlchemy engine:
   - If `engine` provided: use it
   - Else: read from `.env` or `table_json` metadata

### Step B — Load/merge configuration
1. If `table_json` exists: load JSON
2. Merge provided args with JSON (JSON fills missing args only)
3. Validate core options (e.g. `if_exist` in {insert, update, upsert, replace})

### Step C — Cleaning + casting
1. If `clean=True`:
   - call `utils.cleaner.clean_df(df, outlier=outlier)`
2. If `cast=True`:
   - `utils.casting.cast_dataframe()` or equivalent
3. If `dtype` provided:
   - override column dtypes (partial allowed)

### Step D — Profiling / constraints
1. If `auto_profiling=True` and `table_constraints is None`:
   - `profile_dataframe(df)`
   - `get_pk()` to infer PK
   - build `table_constraints = {"pk": [...]} + optional unique
2. Validate provided constraints vs DataFrame columns.

### Step E — Table existence
1. Use SQLAlchemy inspector:
   - `inspect(engine).has_table(table, schema=schema)`

### Step F — Table creation or alignment
1. If table doesn’t exist:
   - use `utils/ddl.df_to_ddl_and_schema()` to build CREATE TABLE
   - execute DDL
2. If table exists:
   - If `add_new_column=True`:
     - use `aligner.analyze` → `ddl_plan` → `execute.apply_ddl_plan()`
   - Else:
     - drop unknown columns from df

### Step G — Write data
Based on `if_exist`:
- `insert`: `router.insert(engine, df, table, chunk)`
- `update`: `router.update(engine, table, df, where, expression)`
- `upsert`: `router.upsert(engine, df, table, constrain=pk, chunk)`
- `replace`: truncate + insert

### Step H — Persist table_json
1. Write schema + constraints + params into `table_json`
2. Update after each execution

## 6) Edge Cases to Handle
- Empty DataFrame → return early
- Missing columns for update / where
- If `if_exist=update` and `where` is None → error
- If `if_exist=upsert` and no PK / constrain → error
- SQL types mismatch without alignment → raise or warn

## 7) Suggested Return Shape
Return dict with:
```
{
  "table": str,
  "row_count": int,
  "mode": str,
  "table_created": bool,
  "columns_added": list,
  "ddl_executed": list,
  "issues": list
}
```

## 8) Minimal Helper APIs to Implement
### utils/cleaner.py
```
clean_df(df: pd.DataFrame, outlier: float = 0.5) -> pd.DataFrame
```

### engine loader
```
load_engine_from_env() -> sqlalchemy.Engine
```

### table_json helper
```
load_table_json(path: str) -> dict
write_table_json(path: str, data: dict) -> None
```

---

If approved, I can now implement these pieces in code.
