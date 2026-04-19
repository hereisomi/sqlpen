## Final revised plan (Oracle-first, multi-dialect) with default “drop extra DataFrame columns” + optional outlier correction (max 5%)

### Design principles
1) **Two method groups**
- **Engine-required methods**: reflection, capability detection, DDL execution, runtime DB queries.
- **Standalone functions**: analysis, mapping, coercion, outlier handling, plan generation (no engine).

2) **Oracle-first**, but with capability-based support for **PostgreSQL, MSSQL, MySQL/MariaDB, SQLite**.

3) **Default safe behavior**
- If the DataFrame contains columns not present in the target table: **drop those DF columns by default** (no DB changes).
- Outlier correction is **optional** and capped at **5% of total rows**.

---

## 1) Single package layout (one cohesive module, internally separated)

### Standalone (no Engine)
- `models.py`
  - `ColumnSpec`, `TableSpec`, `ConstraintSpec`, `IndexSpec`
  - `EngineInfo` (optional in standalone mode)
  - `AnalysisReport`, `CoercionReport`, `AlignmentPlan`, `DDLPlan`
  - `Issue(code, severity, message, context)`

- `type_system.py`
  - `TypeFamily` enum
  - `infer_type_family(sa_type, dialect) -> TypeFamily`
  - `extract_limits(sa_type, dialect) -> length/precision/scale/timezone/etc`
  - Oracle-focused mappings (NUMBER, VARCHAR2 semantics, DATE vs TIMESTAMP, CLOB/BLOB)

- `mapping.py`
  - `normalize_identifier(name, dialect)`
  - `build_column_mapping(df_cols, table_cols, rules=...) -> mapping + confidence + reasons`

- `analyze.py`
  - `analyze(df, table_spec, policies) -> (AnalysisReport, AlignmentPlan, DDLPlan)`
  - Validations: nullability, type mismatch, length overflow, precision/scale overflow, datetime tz issues, constraint coverage (if present in spec)
  - Detect “extra DF columns” and mark them as **planned drop** (default policy)

- `outliers.py` (optional feature)
  - `detect_outliers(df, columns, method="iqr"| "mad"| "zscore", combine_rule="any") -> OutlierResult`
  - `apply_outlier_action(df, outlier_result, action="drop"|"nullify"|"clip") -> (df2, details)`
  - Enforce `outliers.max_pct_total_rows <= 0.05` hard cap

- `coercion.py`
  - `coerce_dataframe(df, table_spec, alignment_plan, policies) -> (df2, CoercionReport)`
  - Includes: strict numeric (int/float/decimal), string length enforcement, bool normalization, datetime parsing + tz policy, json/binary normalization
  - Applies:
    - **Drop extra DF columns** (default)
    - Outlier correction (optional; if enabled and within 5% cap)

- `ddl_plan.py`
  - Generates DDLPlan (add/widen/alter/index) but does not execute
  - Note: dropping DB columns is not part of your default behavior and stays guarded/disabled unless explicitly allowed in future.

### Engine-required
- `engine.py`
  - `get_engine_info(engine)`
  - `get_capabilities(engine)` (DDL/locking/alter limitations per dialect)

- `reflect.py`
  - `reflect_table_spec(engine, schema, table) -> TableSpec`
  - Oracle enrichment (comments, VARCHAR2 semantics if needed via dictionary views, identity detection where possible)

- `execute.py`
  - `apply_ddl_plan(engine, ddl_plan, dry_run=False, lock_timeout=..., statement_timeout=...)`
  - optional helpers: `tail`, `table_activity_status` (if still needed)

---

## 2) Default behaviors (finalized)

### Columns
- **Extra DataFrame columns** (present in DF, not in DB table): **DROP by default**
  - Policy: `columns.extra_df_columns_action = "drop"` (default)
  - Output: recorded in `CoercionReport.dropped_df_columns`

- **Missing DB columns** (present in DB table, not in DF):
  - Analyzer flags:
    - If NOT NULL and no default: error
    - Otherwise: warning/info (depending on policy)
  - No automatic DB drops.

### Outliers (optional)
- `outliers.enabled = False` (default off)
- `outliers.max_pct_total_rows = 0.05` (hard maximum; cannot be configured above 5%)
- `outliers.action = "drop"` (default, when enabled)
- If detected outliers exceed 5% of rows:
  - **Do not apply** correction automatically
  - Add an issue to report: `OUTLIER_RATE_EXCEEDS_CAP`

---

## 3) Unified public API (simple and consistent)

### Engine pipeline (preferred, end-to-end)
```python
table_spec = reflect_table_spec(engine, schema, table)

report, alignment_plan, ddl_plan = analyze(df, table_spec, policies)

df2, coercion_report = coerce_dataframe(df, table_spec, alignment_plan, policies)

# Optional: apply schema updates if enabled by policy
exec_report = apply_ddl_plan(engine, ddl_plan, dry_run=policies.ddl.dry_run)
```

### Standalone pipeline (no engine)
```python
report, alignment_plan, ddl_plan = analyze(df, table_spec, policies)
df2, coercion_report = coerce_dataframe(df, table_spec, alignment_plan, policies)
```

---

## 4) Key enhancements included in this final plan

1) **Single canonical type system** shared by analyzer + coercer + planner  
2) **Oracle-first semantics** (NUMBER, DATE/TIMESTAMP, CLOB/BLOB, VARCHAR2 length semantics)  
3) **Policy-driven coercion** (truncate vs reject, rounding, tz assumptions)  
4) **CoercionReport + AnalysisReport** with counts, samples, and actions taken  
5) **Optional outlier correction** with strict global cap (≤ 5% rows) and default action “drop”  
6) **Dialect capabilities** to prevent unsafe DDL operations across MySQL/SQLite/etc.  
7) **Default safe behavior for DF columns**: drop extras, no DB column drops.

---

## 5) Implementation phases (revised, minimal risk)

### Phase 1: Foundation
- Implement `models.py`, `type_system.py`, `policies.py`
- Write unit tests for type-family mapping (Oracle + others)

### Phase 2: Reflection (engine)
- Implement `reflect_table_spec()` for all dialects
- Add Oracle enrichment hooks

### Phase 3: Analyzer (standalone)
- Implement `analyze()` producing:
  - mapping suggestions
  - “extra DF columns planned drop” list
  - constraint checks
  - outlier detection results (only if enabled)

### Phase 4: Coercion + Outliers
- Implement `coerce_dataframe()`
- Implement `outliers.py` and integrate with coercion pipeline
- Ensure reports capture dropped DF columns + dropped outlier rows separately

### Phase 5: DDL planner/executor (optional)
- Implement safe DDLPlan generation and apply
- Keep DDL execution off by default (dry-run recommended)
