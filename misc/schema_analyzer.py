"""
schema_analyzer.py

Reusable module to analyze a DB table schema and compare it
with a pandas DataFrame, producing a structured report.

Features:
    - Engine & connection info
    - Table existence, comments, columns, constraints
    - DataFrame vs table mapping and validation
    - Dialect-specific checks
    - Optional FK integrity checks
    - Auto-generated 'how to correct' suggestions

Dependencies:
    sqlalchemy
    pandas
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.sql.sqltypes import Numeric, DateTime, Boolean


# ---------- Data classes for structured report ----------

@dataclass
class EngineInfo:
    dialect: str
    driver: str
    url: str
    connect_ok: bool
    connect_error: Optional[str] = None


@dataclass
class ColumnInfo:
    name: str
    type_str: str
    sqlalchemy_type: str
    nullable: bool
    default: Optional[str]
    autoincrement: Optional[bool]
    comment: Optional[str]
    is_numeric: bool
    is_datetime: bool
    is_boolean: bool
    length: Optional[int]
    precision: Optional[int]
    scale: Optional[int]
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    raw_type: Any = field(default=None, repr=False)  # NEW: For strict type checking logic


@dataclass
class ConstraintInfo:
    # Inspector returns SQLAlchemy-reflected constraint objects/dicts; accept Any to
    # avoid strict type incompatibilities from various SQLAlchemy versions.
    primary_key: Any
    foreign_keys: List[Any]
    unique_constraints: List[Any]
    check_constraints: List[Any]
    indexes: List[Any]


@dataclass
class DataFrameInfo:
    columns: List[str]
    dtypes: Dict[str, str]
    null_counts: Dict[str, int]
    sample_rows: pd.DataFrame


@dataclass
class MappingInfo:
    df_to_sql: Dict[str, str]
    missing_sql_cols: List[str]
    extra_df_cols: List[str]
    not_null_violations: Dict[str, int]
    length_violations: Dict[str, int]
    type_warnings: List[str]
    suggestions: List[str] = field(default_factory=list)  # NEW


@dataclass
class ValidationSummary:
    not_null_ok: bool
    unique_ok: bool
    fk_validation_run: bool
    fk_violations: Dict[str, int]  # NEW: per-FK issue count
    issues: List[str]
    suggestions: List[str] = field(default_factory=list)  # NEW


@dataclass
class DialectChecks:  # NEW
    dialect: str
    issues: List[str]
    suggestions: List[str]


@dataclass
class TableAnalysisReport:
    engine_info: EngineInfo
    table_exists: bool
    schema: Optional[str]
    table_name: str
    table_comment: Optional[str]
    columns: Dict[str, ColumnInfo]
    constraints: ConstraintInfo
    df_info: Optional[DataFrameInfo]
    mapping: Optional[MappingInfo]
    validation: Optional[ValidationSummary]
    dialect_checks: Optional[DialectChecks]  # NEW

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        if self.df_info is not None:
            result["df_info"]["sample_rows"] = (
                self.df_info.sample_rows.to_dict(orient="records")
            )
        result["columns"] = {k: asdict(v) for k, v in self.columns.items()}
        return result


# ---------- Core Analyzer Class ----------

class SchemaAnalyzer:
    """
    Analyze DB schema and a DataFrame against a target table.
    """

    def __init__(self, engine: Engine | Connection):
        self.engine = engine # This acts as the 'bind'
        self.inspector = inspect(engine)

    # ----- Engine Info -----

    def _gather_engine_info(self) -> EngineInfo:
        # Resolve actual Engine object for property access
        if isinstance(self.engine, Engine):
            real_engine = self.engine
        else:
            # Assume it's a Connection
            real_engine = self.engine.engine

        dialect = real_engine.dialect.name
        driver = real_engine.dialect.driver or ""
        url = str(real_engine.url)
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            host_part = rest.split("@", 1)[1] if "@" in rest else ""
            url = f"{scheme}://***:***@{host_part}" if host_part else f"{scheme}://***"

        connect_ok = True
        connect_error = None
        try:
            # If we are already connected (Connection), this check is trivial/different
            if isinstance(self.engine, Connection):
                if self.engine.closed:
                    connect_ok = False
                    connect_error = "Connection Closed"
            else:
                with self.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
        except Exception as e:
            connect_ok = False
            connect_error = repr(e)

        return EngineInfo(
            dialect=dialect,
            driver=driver,
            url=url,
            connect_ok=connect_ok,
            connect_error=connect_error,
        )

    # ----- Table / Column Introspection -----

    def _table_exists(self, schema: Optional[str], table_name: str) -> bool:
        tables = self.inspector.get_table_names(schema=schema)
        return table_name in tables

    def _gather_table_comment(self, schema: Optional[str], table_name: str) -> Optional[str]:
        try:
            c = self.inspector.get_table_comment(table_name, schema=schema)
            if isinstance(c, dict):
                return c.get("text")
            return str(c) if c else None
        except Exception:
            return None

    def _gather_columns(
        self, schema: Optional[str], table_name: str
    ) -> Dict[str, ColumnInfo]:
        raw_cols = self.inspector.get_columns(table_name, schema=schema)
        result: Dict[str, ColumnInfo] = {}

        for col in raw_cols:
            name = col["name"]
            sqla_type = col["type"]
            type_str = type(sqla_type).__name__

            length = getattr(sqla_type, "length", None)
            precision = getattr(sqla_type, "precision", None)
            scale = getattr(sqla_type, "scale", None)

            is_num = isinstance(sqla_type, Numeric)
            is_dt = isinstance(sqla_type, DateTime)
            is_bool = isinstance(sqla_type, Boolean)

            ci = ColumnInfo(
                name=name,
                type_str=type_str,
                sqlalchemy_type=str(sqla_type),
                nullable=bool(col.get("nullable", True)),
                default=str(col.get("default")) if col.get("default") is not None else None,
                autoincrement=col.get("autoincrement"),
                comment=col.get("comment"),
                is_numeric=is_num,
                is_datetime=is_dt,
                is_boolean=is_bool,
                length=length,
                precision=precision,
                scale=scale,
                issues=[],
                suggestions=[],
                raw_type=sqla_type,  # Populate raw type
            )
            result[name] = ci

        return result

    def _gather_constraints(
        self, schema: Optional[str], table_name: str
    ) -> ConstraintInfo:
        pk = self.inspector.get_pk_constraint(table_name, schema=schema) or {}
        fks = self.inspector.get_foreign_keys(table_name, schema=schema) or []
        uniques = self.inspector.get_unique_constraints(table_name, schema=schema) or []
        checks = self.inspector.get_check_constraints(table_name, schema=schema) or []
        indexes = self.inspector.get_indexes(table_name, schema=schema) or []

        return ConstraintInfo(
            primary_key=pk,
            foreign_keys=fks,
            unique_constraints=uniques,
            check_constraints=checks,
            indexes=indexes,
        )

    # ----- DataFrame analysis -----

    def _gather_df_info(self, df: pd.DataFrame) -> DataFrameInfo:
        dtypes = {c: str(dt) for c, dt in df.dtypes.items()}
        null_counts = df.isnull().sum().to_dict()
        sample = df.head(10).copy()

        return DataFrameInfo(
            columns=list(df.columns),
            dtypes=dtypes, # pyright: ignore[reportArgumentType]
            null_counts={k: int(v) for k, v in null_counts.items()},
            sample_rows=sample,
        )

    # ----- Mapping & Validation -----

    def _build_column_mapping(
        self,
        df: pd.DataFrame,
        columns: Dict[str, ColumnInfo],
    ) -> MappingInfo:
        sql_cols = list(columns.keys())
        map_lower = {c.lower(): c for c in sql_cols}

        df_to_sql: Dict[str, str] = {}
        for dc in df.columns:
            match = map_lower.get(dc.lower())
            if match:
                df_to_sql[dc] = match

        missing_sql = [c for c in sql_cols if c not in df_to_sql.values()]
        extra_df = [c for c in df.columns if c not in df_to_sql]

        not_null_violations: Dict[str, int] = {}
        for sql_col, info in columns.items():
            if not info.nullable:
                df_cols_mapped = [k for k, v in df_to_sql.items() if v == sql_col]
                if not df_cols_mapped:
                    not_null_violations[sql_col] = len(df)
                    continue
                df_col = df_cols_mapped[0]
                if df_col in df.columns:
                    cnt = int(df[df_col].isnull().sum())
                    if cnt > 0:
                        not_null_violations[sql_col] = cnt

        length_violations: Dict[str, int] = {}
        for df_col, sql_col in df_to_sql.items():
            col_info = columns[sql_col]
            if col_info.length and str(df[df_col].dtype) in ("object", "string"):
                s = df[df_col].astype(str)
                over = s[s.str.len() > col_info.length]
                if not over.empty:
                    length_violations[sql_col] = len(over)

        type_warnings: List[str] = []
        for df_col, sql_col in df_to_sql.items():
            col_info = columns[sql_col]
            df_dtype = str(df[df_col].dtype)

            if col_info.is_numeric and not df_dtype.startswith(("int", "float", "Int", "Float", "decimal")):
                type_warnings.append(
                    f"Column {df_col} mapped to numeric SQL column {sql_col}, "
                    f"but DataFrame dtype is {df_dtype}"
                )
            if col_info.is_datetime and "datetime64" not in df_dtype:
                type_warnings.append(
                    f"Column {df_col} mapped to datetime SQL column {sql_col}, "
                    f"but DataFrame dtype is {df_dtype}"
                )
            if col_info.is_boolean and df_dtype not in ("bool", "boolean"):
                type_warnings.append(
                    f"Column {df_col} mapped to boolean SQL column {sql_col}, "
                    f"but DataFrame dtype is {df_dtype}"
                )

        suggestions: List[str] = []

        if missing_sql:
            suggestions.append(
                "Missing SQL columns in DataFrame: "
                + ", ".join(missing_sql)
                + ". Consider adding these columns in the ETL with default/derived values."
            )
        if extra_df:
            suggestions.append(
                "Extra DataFrame columns not present in SQL: "
                + ", ".join(extra_df)
                + ". Consider dropping them before load or adding columns to the table."
            )

        if not_null_violations:
            s_cols = ", ".join(f"{c}({cnt} nulls)" for c, cnt in not_null_violations.items())
            suggestions.append(
                f"NOT NULL violations detected in: {s_cols}. "
                "Fill missing values, drop invalid rows, or review column nullability."
            )

        if length_violations:
            s_cols = ", ".join(f"{c}({cnt} too long)" for c, cnt in length_violations.items())
            suggestions.append(
                f"Length violations detected in: {s_cols}. "
                "Truncate strings in ETL or widen VARCHAR length via ALTER TABLE."
            )

        if type_warnings:
            suggestions.append(
                "Type mismatches between DataFrame and SQL columns. "
                "Cast DataFrame columns to appropriate types before load."
            )

        return MappingInfo(
            df_to_sql=df_to_sql,
            missing_sql_cols=missing_sql,
            extra_df_cols=extra_df,
            not_null_violations=not_null_violations,
            length_violations=length_violations,
            type_warnings=type_warnings,
            suggestions=suggestions,
        )

    def _run_fk_validation(
        self,
        df: pd.DataFrame,
        constraints: ConstraintInfo,
        mapping: MappingInfo,
        schema: Optional[str],
    ) -> Tuple[bool, Dict[str, int], List[str]]:
        """
        Optional FK integrity check:
        For each FK, check if DF values exist in parent table.
        Returns (fk_validation_run, fk_violations dict, issues list)
        """
        issues: List[str] = []
        fk_violations: Dict[str, int] = {}
        if not constraints.foreign_keys:
            return False, fk_violations, issues

        fk_validation_run = True

        fk_validation_run = True

        # Use either Engine.connect() or an existing Connection object
        if isinstance(self.engine, Engine):
            conn_ctx = self.engine.connect()
            using_context = True
        else:
            conn_ctx = self.engine
            using_context = False

        try:
            if using_context:
                with conn_ctx as conn:
                    fk_source = list(constraints.foreign_keys)
                    for fk in fk_source:
                        fk_name = fk.get("name") or "unnamed_fk"
                        local_cols = fk.get("constrained_columns") or []
                        ref_table = fk.get("referred_table")
                        ref_schema = fk.get("referred_schema") or schema
                        ref_cols = fk.get("referred_columns") or []

                        if not local_cols or not ref_table or not ref_cols:
                            continue
                        if len(local_cols) != len(ref_cols):
                            continue  # compound mismatches not handled in this simple version

                        local_sql_col = local_cols[0]
                        ref_sql_col = ref_cols[0]

                        # Map SQL local column to DF column
                        df_cols = [
                            dfc for dfc, sqc in mapping.df_to_sql.items() if sqc == local_sql_col
                        ]
                        if not df_cols:
                            continue
                        df_col = df_cols[0]

                        non_null_vals = df[df_col].dropna().unique()
                        if len(non_null_vals) == 0:
                            continue

                        # Quote identifiers when possible and avoid huge IN-lists by chunking
                        dialect_name = self.engine.dialect.name
                        if ref_schema and dialect_name != "sqlite":
                            full_table_name = f'"{ref_schema}"."{ref_table}"'
                        else:
                            full_table_name = f'"{ref_table}"' if dialect_name != "sqlite" else ref_table

                        ref_col_quoted = f'"{ref_sql_col}"' if dialect_name != "sqlite" else ref_sql_col

                        parent_vals = set()
                        vals_list = list(non_null_vals)
                        chunk_size = 500
                        try:
                            for start in range(0, len(vals_list), chunk_size):
                                chunk = vals_list[start:start + chunk_size]
                                placeholders = ", ".join([":v" + str(i) for i in range(len(chunk))])
                                sql = (
                                    f"SELECT DISTINCT {ref_col_quoted} AS val "
                                    f"FROM {full_table_name} "
                                    f"WHERE {ref_col_quoted} IN ({placeholders})"
                                )
                                params = {f"v{i}": chunk[i] for i in range(len(chunk))}
                                res = conn.execute(text(sql), params).fetchall()
                                parent_vals.update({row._mapping["val"] if hasattr(row, "_mapping") else row[0] for row in res})
                        except Exception as e:
                            issues.append(f"Error running FK validation for {fk_name}: {repr(e)}")
                            continue

                        missing_mask = ~df[df_col].isin(parent_vals) & df[df_col].notna()
                        missing_count = int(missing_mask.sum())
                        if missing_count > 0:
                            fk_violations[fk_name] = missing_count
                            issues.append(
                                f"FK {fk_name} violation: {missing_count} rows in column {df_col} "
                                f"have values not found in {ref_schema}.{ref_table}.{ref_sql_col}"
                            )
            else:
                conn = conn_ctx
                for fk in list(constraints.foreign_keys):
                    fk_name = fk.get("name") or "unnamed_fk"
                    local_cols = fk.get("constrained_columns") or []
                    ref_table = fk.get("referred_table")
                    ref_schema = fk.get("referred_schema") or schema
                    ref_cols = fk.get("referred_columns") or []

                    if not local_cols or not ref_table or not ref_cols:
                        continue
                    if len(local_cols) != len(ref_cols):
                        continue

                    local_sql_col = local_cols[0]
                    ref_sql_col = ref_cols[0]

                    df_cols = [dfc for dfc, sqc in mapping.df_to_sql.items() if sqc == local_sql_col]
                    if not df_cols:
                        continue
                    df_col = df_cols[0]

                    non_null_vals = df[df_col].dropna().unique()
                    if len(non_null_vals) == 0:
                        continue

                    dialect_name = self.engine.dialect.name
                    if ref_schema and dialect_name != "sqlite":
                        full_table_name = f'"{ref_schema}"."{ref_table}"'
                    else:
                        full_table_name = f'"{ref_table}"' if dialect_name != "sqlite" else ref_table

                    ref_col_quoted = f'"{ref_sql_col}"' if dialect_name != "sqlite" else ref_sql_col

                    parent_vals = set()
                    vals_list = list(non_null_vals)
                    chunk_size = 500
                    try:
                        for start in range(0, len(vals_list), chunk_size):
                            chunk = vals_list[start:start + chunk_size]
                            placeholders = ", ".join([":v" + str(i) for i in range(len(chunk))])
                            sql = (
                                f"SELECT DISTINCT {ref_col_quoted} AS val "
                                f"FROM {full_table_name} "
                                f"WHERE {ref_col_quoted} IN ({placeholders})"
                            )
                            params = {f"v{i}": chunk[i] for i in range(len(chunk))}
                            res = conn.execute(text(sql), params).fetchall()
                            parent_vals.update({row._mapping["val"] if hasattr(row, "_mapping") else row[0] for row in res})
                    except Exception as e:
                        issues.append(f"Error running FK validation for {fk_name}: {repr(e)}")
                        continue

                    missing_mask = ~df[df_col].isin(parent_vals) & df[df_col].notna()
                    missing_count = int(missing_mask.sum())
                    if missing_count > 0:
                        fk_violations[fk_name] = missing_count
                        issues.append(
                            f"FK {fk_name} violation: {missing_count} rows in column {df_col} "
                            f"have values not found in {ref_schema}.{ref_table}.{ref_sql_col}"
                        )

        finally:
            # If we opened a connection via Engine.connect(), the context manager closed it.
            pass

        return fk_validation_run, fk_violations, issues

    def _validate_constraints(
        self,
        df: pd.DataFrame,
        columns: Dict[str, ColumnInfo],
        constraints: ConstraintInfo,
        mapping: MappingInfo,
        schema: Optional[str],
        run_fk_checks: bool,
    ) -> ValidationSummary:
        # pylint: disable=unused-argument
        issues: List[str] = []

        # NOT NULL
        not_null_ok = len(mapping.not_null_violations) == 0
        if not not_null_ok:
            for col, cnt in mapping.not_null_violations.items():
                issues.append(f"NOT NULL violation: {col} has {cnt} null rows in DataFrame")

        # UNIQUE constraints
        unique_ok = True
        for uq in constraints.unique_constraints:
            cols = uq.get("column_names") or []
            if not cols:
                continue
            df_cols = []
            for sql_col in cols:
                matches = [dfc for dfc, sqc in mapping.df_to_sql.items() if sqc == sql_col]
                if matches:
                    df_cols.append(matches[0])

            if not df_cols:
                continue
            dups = df.duplicated(subset=df_cols)
            dup_cnt = int(dups.sum())
            if dup_cnt > 0:
                unique_ok = False
                issues.append(
                    f"UNIQUE constraint violation on {cols}: {dup_cnt} duplicate rows in DataFrame"
                )

        # FK constraints (optional)
        fk_validation_run = False
        fk_violations: Dict[str, int] = {}
        fk_issues: List[str] = []
        if run_fk_checks:
            fk_validation_run, fk_violations, fk_issues = self._run_fk_validation(
                df, constraints, mapping, schema
            )
            issues.extend(fk_issues)

        # Length & types (from mapping)
        if mapping.length_violations:
            for col, cnt in mapping.length_violations.items():
                issues.append(
                    f"Length violation: {col} has {cnt} rows exceeding column length"
                )

        for w in mapping.type_warnings:
            issues.append("Type warning: " + w)

        # Suggestions
        suggestions: List[str] = []

        if not_null_ok is False:
            suggestions.append(
                "Resolve NOT NULL violations by filling missing values or "
                "dropping rows before load, or by relaxing NOT NULL on the column."
            )
        if not unique_ok:
            suggestions.append(
                "Resolve UNIQUE constraint violations by deduplicating DataFrame rows "
                "on the business key columns or by fixing upstream data."
            )
        if fk_violations:
            fk_list = ", ".join(f"{name}({cnt} rows)" for name, cnt in fk_violations.items())
            suggestions.append(
                f"Fix FK violations ({fk_list}) by ensuring parent rows exist in referenced tables "
                "before loading children, or by correcting FK values in the source data."
            )

        if mapping.type_warnings:
            suggestions.append(
                "Address type warnings by casting DataFrame columns to types "
                "compatible with the target SQL columns (e.g. to_datetime, astype(int))."
            )

        return ValidationSummary(
            not_null_ok=not_null_ok,
            unique_ok=unique_ok,
            fk_validation_run=fk_validation_run,
            fk_violations=fk_violations,
            issues=issues,
            suggestions=suggestions,
        )

    # ----- Dialect-specific checks -----

    def _run_dialect_checks(
        self,
        schema: Optional[str],
        table_name: str,
        columns: Dict[str, ColumnInfo],
    ) -> DialectChecks:
        dialect = self.engine.dialect.name.lower()
        issues: List[str] = []
        suggestions: List[str] = []

        # Oracle
        if dialect == "oracle":
            # Empty string = NULL
            suggestions.append(
                "Oracle: Empty strings are treated as NULL. "
                "Ensure ETL normalizes empty strings to NULL consistently."
            )

            # Identifier length
            long_cols = [c for c in columns.keys() if len(c) > 30]
            if long_cols:
                issues.append(
                    "Oracle identifier length > 30 detected in columns: " + ", ".join(long_cols)
                )
                suggestions.append(
                    "Shorten Oracle column names to 30 characters or fewer, "
                    "and use a mapping layer in ETL from logical to physical names."
                )

        # MySQL / MariaDB
        elif dialect in ("mysql", "mariadb"):
            # TINYINT(1) boolean convention
            for c in columns.values():
                if "TINYINT(1)" in c.sqlalchemy_type.upper() and not c.is_boolean:
                    issues.append(
                        f"MySQL type TINYINT(1) detected in column {c.name} "
                        "which is often used as boolean."
                    )
                    suggestions.append(
                        f"Treat column {c.name} as boolean in ETL (map True/False to 1/0), "
                        "or change to BOOLEAN type if supported."
                    )

            # Zero-date detection – light heuristic on DATE/TIMESTAMP columns
            if isinstance(self.engine, Engine):
                with self.engine.connect() as conn:
                    for c in columns.values():
                        if "DATE" in c.sqlalchemy_type.upper() or "TIME" in c.sqlalchemy_type.upper():
                            full_table = (
                                f"{schema}.{table_name}" if schema and dialect != "sqlite" else table_name
                            )
                            sql = (
                                f"SELECT COUNT(*) AS cnt FROM {full_table} "
                                f"WHERE {c.name} IN ('0000-00-00', '0000-00-00 00:00:00')"
                            )
                            try:
                                cnt = conn.execute(text(sql)).scalar()
                                if cnt and cnt > 0:
                                    issues.append(
                                        f"MySQL zero-date values detected in column {c.name}: {cnt} rows."
                                    )
                                    suggestions.append(
                                        f"Clean zero-date values in {c.name} "
                                        "by converting '0000-00-00' to NULL or a real date "
                                        "before loading into stricter systems (e.g. pandas, PostgreSQL)."
                                    )
                            except Exception:
                                # ignore if column not compatible
                                pass
            else:
                conn = self.engine
                for c in columns.values():
                    if "DATE" in c.sqlalchemy_type.upper() or "TIME" in c.sqlalchemy_type.upper():
                        full_table = (
                            f"{schema}.{table_name}" if schema and dialect != "sqlite" else table_name
                        )
                        sql = (
                            f"SELECT COUNT(*) AS cnt FROM {full_table} "
                            f"WHERE {c.name} IN ('0000-00-00', '0000-00-00 00:00:00')"
                        )
                        try:
                            cnt = conn.execute(text(sql)).scalar()
                            if cnt and cnt > 0:
                                issues.append(
                                    f"MySQL zero-date values detected in column {c.name}: {cnt} rows."
                                )
                                suggestions.append(
                                    f"Clean zero-date values in {c.name} "
                                    "by converting '0000-00-00' to NULL or a real date "
                                    "before loading into stricter systems (e.g. pandas, PostgreSQL)."
                                )
                        except Exception:
                            # ignore if column not compatible
                            pass

        # MSSQL
        elif dialect in ("mssql", "mssql+pyodbc", "mssql+pymsql"):
            # BIT detection
            for c in columns.values():
                if "BIT" in c.sqlalchemy_type.upper() and not c.is_boolean:
                    issues.append(
                        f"SQL Server BIT column {c.name} detected; used as boolean."
                    )
                    suggestions.append(
                        f"Ensure DataFrame column mapped to {c.name} is boolean "
                        "or integer 0/1 before load."
                    )

        # PostgreSQL
        elif dialect == "postgresql":
            # Arrays and JSON types
            for c in columns.values():
                if "ARRAY" in c.sqlalchemy_type.upper():
                    suggestions.append(
                        f"PostgreSQL array column {c.name} detected. "
                        "Ensure DataFrame values are Python lists (or compatible sequences) "
                        "before load."
                    )
                if "JSON" in c.sqlalchemy_type.upper():
                    suggestions.append(
                        f"PostgreSQL JSON/JSONB column {c.name} detected. "
                        "Ensure DataFrame values are dicts/lists or JSON strings "
                        "serializable via json.dumps."
                    )

        # SQLite
        elif dialect == "sqlite":
            suggestions.append(
                "SQLite uses dynamic typing and type affinity. "
                "Validate data types in ETL explicitly and cast DataFrame columns "
                "to desired types before writing."
            )
            suggestions.append(
                "SQLite has limited ALTER TABLE support; complex schema changes "
                "often require creating a new table, copying data, and renaming."
            )

        return DialectChecks(
            dialect=dialect,
            issues=issues,
            suggestions=suggestions,
        )

    # ----- Public API -----

    def analyze_table(
        self,
        schema: Optional[str],
        table_name: str,
        df: Optional[pd.DataFrame] = None,
        run_fk_checks: bool = True,   # NEW: control FK validation
    ) -> TableAnalysisReport:
        """
        Analyze a DB table (and optionally a DataFrame) and return a structured report.

        Parameters
        ----------
        schema : str or None
            Schema/owner name, or None for default.
        table_name : str
            Target table name.
        df : pandas.DataFrame or None
            DataFrame to validate/match against the table. If None, only schema report is returned.
        run_fk_checks : bool
            If True, runs optional FK integrity checks by querying parent tables.
        """
        engine_info = self._gather_engine_info()
        exists = self._table_exists(schema, table_name)

        if not exists:
            return TableAnalysisReport(
                engine_info=engine_info,
                table_exists=False,
                schema=schema,
                table_name=table_name,
                table_comment=None,
                columns={},
                constraints=ConstraintInfo(
                    primary_key={},
                    foreign_keys=[],
                    unique_constraints=[],
                    check_constraints=[],
                    indexes=[],
                ),
                df_info=self._gather_df_info(df) if df is not None else None,
                mapping=None,
                validation=None,
                dialect_checks=None,
            )

        table_comment = self._gather_table_comment(schema, table_name)
        columns = self._gather_columns(schema, table_name)
        constraints = self._gather_constraints(schema, table_name)
        dialect_checks = self._run_dialect_checks(schema, table_name, columns)

        df_info: Optional[DataFrameInfo] = None
        mapping: Optional[MappingInfo] = None
        validation: Optional[ValidationSummary] = None

        if df is not None:
            df_info = self._gather_df_info(df)
            mapping = self._build_column_mapping(df, columns)
            validation = self._validate_constraints(
                df, columns, constraints, mapping, schema, run_fk_checks
            )

        return TableAnalysisReport(
            engine_info=engine_info,
            table_exists=True,
            schema=schema,
            table_name=table_name,
            table_comment=table_comment,
            columns=columns,
            constraints=constraints,
            df_info=df_info,
            mapping=mapping,
            validation=validation,
            dialect_checks=dialect_checks,
        )


# ---------- Convenience function ----------

def analyze_table(
    engine: Engine,
    schema: Optional[str],
    table_name: str,
    df: Optional[pd.DataFrame] = None,
    run_fk_checks: bool = True,
) -> TableAnalysisReport:
    analyzer = SchemaAnalyzer(engine)
    return analyzer.analyze_table(schema=schema, table_name=table_name, df=df, run_fk_checks=run_fk_checks)