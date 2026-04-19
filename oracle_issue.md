# Oracle Edge-Case Issues

## Summary
Oracle is the strictest dialect in the SqlSql pipeline. Even with a dedicated DDL generator (`utils/ddl_orc.py`) and aggressive sanitization, Oracle still rejects edge-case CSVs due to identifier and alignment mismatches.

## Root Causes

### 1. Identifier Sanitization vs DDL Generation Mismatch
- The generic `sanitize_cols()` produces lowercase, underscored names (`to_`, `n123col`).
- Oracle DDL generator (`ddl_orc.py`) expects already-sanitized, uppercase column names.
- The pipeline runs `_oracle_uppercase_df_and_constraints` after DDL generation, causing misalignment between DDL and DataFrame columns.

### 2. Schema Reflection Alignment Errors
- After DDL execution, the pipeline reflects the table schema to align DataFrame columns.
- Oracle stores unquoted identifiers in uppercase; the reflected columns are uppercase.
- The DataFrame columns after sanitization are lowercase (`to_`), leading to `MISSING_DB_COLUMN` errors.

### 3. Quoted Identifiers and Reserved Words
- Oracle rejects quoted identifiers with spaces (`"USER NAME"`) in generated DDL strings, even though they work manually.
- Reserved words (`VALUE`, `TO`, `DESC`, `COUNT`, etc.) require renaming; current sanitization appends `_` but timing is off.

### 4. Newline/Whitespace Sensitivity
- Oracle parser rejects multi-line DDL strings (`ORA-00922`) even when semicolon-stripped.
- Manual single-line DDL works, but pipeline-generated DDL often contains hidden newlines.

## Fixes Attempted

1. **Expanded Oracle `RESERVED_WORDS`** (added `COUNT`, `VALUE`, `TO`, `DESC`).
2. **Created `utils/ddl_orc.py`** with strict sanitizer and single-line DDL.
3. **Delegated Oracle DDL generation** to `ddl_orc.py` in `ddl_create.py`.
4. **Forced uppercase column names** for Oracle (`_oracle_uppercase_df_and_constraints`).
5. **Whitespace stripping** (`' '.join(ddl.split())`).
6. **Column alignment via regex extraction** (failed due to timing).
7. **Direct column renaming to sanitized names** (still misaligned with reflection).

## Remaining Blockers

- **Timing**: Sanitization, DDL generation, and reflection happen in different orders for Oracle.
- **Reflection**: Oracle returns uppercase column names; DataFrame columns are lowercase after sanitization.
- **DDL String Format**: Even single-line DDL can contain invisible newlines that Oracle rejects.

## Workaround

For Oracle, pre-sanitize CSV column names outside the pipeline:
- Replace spaces with underscores.
- Prefix numeric-starting columns with `N`.
- Append `_` to reserved words.
- Use only uppercase identifiers.

Then run with `--apply-ddl` and `ops insert` without relying on the pipeline sanitization.

## Recommendation

Either:
- Accept that Oracle requires pre-sanitized CSVs and document the naming rules.
- Or, refactor the pipeline to perform sanitization **before** DDL generation and **after** reflection for Oracle, ensuring consistent casing throughout the flow.

---

## Changes Made

### Files Created
- `utils/ddl_orc.py`: Oracle-specific DDL generator with strict sanitization and single-line output.

### Files Modified
- `utils/ddl_create.py`:
  - Expanded Oracle `RESERVED_WORDS` set (added `COUNT`, `VALUE`, `TO`, `DESC`).
  - Added delegation to `ddl_orc.build_create_table` for Oracle DDL generation.
  - Added attempts at column alignment via regex extraction and direct renaming.
  - Added whitespace stripping for Oracle DDL strings.
  - Modified numeric-prefix handling in `sanitize_cols` to prefix with `N` for Oracle.
  - Added Oracle-specific branches in `escape_identifier` and `df_ddl`.

### Modules Corrupted During Debugging
- `utils/ddl_create.py` became syntactically corrupted multiple times due to repeated edits:
  - Indentation errors around Oracle DDL generation block.
  - Duplicate and misplaced statements (e.g., stray `raise ValueError` lines).
  - Incorrect ordering of validation and DDL generation steps.
  - Return value mismatch (`return ddl, ddl_indexes` instead of `return ddl, meta, df`).
- These corruptions required manual cleanup and rewrites of the Oracle-specific sections.

### Functional Impact
- Other dialects (PostgreSQL, MySQL/MariaDB, MSSQL, SQLite) remain unaffected.
- Oracle path is isolated but currently non-functional for edge-case CSVs due to alignment issues.

---

## Successful Edge-Case Scenarios (Other Databases)

### PostgreSQL
- **Reserved words**: Properly quoted (`"SELECT"`, `"ORDER"`, etc.).
- **Mixed case**: Preserved via quoting.
- **Spaces in names**: Accepted when quoted (`"USER NAME"`).
- **Numeric prefixes**: Handled without issues.
- **Result**: Full INSERT/UPSERT/UPDATE harness passes on `edge_case.csv`.

### MySQL/MariaDB
- **Reserved words**: Quoted with backticks (``SELECT``).
- **Mixed case**: Handled via backticks.
- **Spaces in names**: Not supported; sanitized to underscores.
- **Numeric prefixes**: Handled.
- **Result**: Full harness passes.

### MSSQL
- **Reserved words**: Bracketed (`[SELECT]`).
- **Mixed case**: Handled via brackets.
- **Spaces in names**: Not supported; sanitized to underscores.
- **PK NOT NULL enforcement**: Fixed to avoid constraint errors.
- **Result**: Full harness passes.

### SQLite
- **Reserved words**: Quoted (`"SELECT"`).
- **Mixed case**: Preserved via quoting.
- **Spaces in names**: Accepted when quoted.
- **Numeric prefixes**: Handled.
- **Result**: Full harness passes.

### Common Success Factors
- DDL generation works without newlines or semicolons.
- Identifier quoting is dialect-appropriate and applied consistently.
- Schema reflection aligns with DataFrame columns.
- No strict identifier length or character restrictions beyond standard SQL.

### Oracle Contrast
- Oracle requires stricter sanitization (no spaces, uppercase, no leading digits).
- Quoted identifiers with spaces cause `ORA-00922` in generated DDL.
- Reflection returns uppercase names, causing misalignment with sanitized lowercase DataFrame columns.
- Even single-line DDL can fail due to hidden newlines or whitespace.
