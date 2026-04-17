"""
Schema introspection, column alignment, and type coercion.

Replaces the broken SchemaAligner from the old crud — the critical fix is
passing actual SQLAlchemy type *objects* (not strings) to isinstance checks.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import NoSuchTableError, SQLAlchemyError
from sqlalchemy.sql import sqltypes as sat

try:
    from dateutil import parser as _du
except ImportError:
    _du = None

from .fingerprint import build_fingerprint
from .types import CrudConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dialect-specific type imports (safe fallback)
# ---------------------------------------------------------------------------
try:
    from sqlalchemy.dialects.oracle import (
        VARCHAR2, NVARCHAR2, CHAR as ORA_CHAR, NCHAR, CLOB, NCLOB,
        NUMBER, FLOAT as ORA_FLOAT, BINARY_DOUBLE, BINARY_FLOAT,
        DATE as ORA_DATE, TIMESTAMP as ORA_TIMESTAMP,
        RAW, BLOB as ORA_BLOB, LONG, INTERVAL,
    )
    _ORA = True
except ImportError:
    _ORA = False

try:
    from sqlalchemy.dialects.mysql import (
        TINYINT, SMALLINT as MY_SMALLINT, MEDIUMINT,
        INTEGER as MY_INT, BIGINT as MY_BIGINT,
        FLOAT as MY_FLOAT, DOUBLE as MY_DOUBLE, DECIMAL as MY_DECIMAL,
        VARCHAR as MY_VARCHAR, CHAR as MY_CHAR, TEXT as MY_TEXT,
        TINYTEXT, MEDIUMTEXT, LONGTEXT,
        DATETIME as MY_DATETIME, TIMESTAMP as MY_TIMESTAMP,
        DATE as MY_DATE, TIME as MY_TIME,
        BLOB as MY_BLOB, TINYBLOB, MEDIUMBLOB, LONGBLOB,
        JSON as MY_JSON, ENUM as MY_ENUM, SET as MY_SET,
    )
    _MY = True
except ImportError:
    _MY = False

try:
    from sqlalchemy.dialects.mssql import (
        TINYINT as MS_TINYINT, SMALLINT as MS_SMALLINT,
        INTEGER as MS_INT, BIGINT as MS_BIGINT,
        FLOAT as MS_FLOAT, REAL as MS_REAL, DECIMAL as MS_DECIMAL,
        MONEY, SMALLMONEY,
        VARCHAR as MS_VARCHAR, CHAR as MS_CHAR, NVARCHAR, NCHAR as MS_NCHAR,
        TEXT as MS_TEXT, NTEXT,
        DATETIME as MS_DATETIME, DATETIME2, SMALLDATETIME,
        DATE as MS_DATE, TIME as MS_TIME, DATETIMEOFFSET,
        BINARY as MS_BINARY, VARBINARY as MS_VARBINARY, IMAGE,
        BIT,
    )
    _MS = True
except ImportError:
    _MS = False


# ---------------------------------------------------------------------------
# Column metadata wrapper
# ---------------------------------------------------------------------------
class ColumnInfo:
    """Lightweight wrapper around SQLAlchemy inspection dict."""

    __slots__ = (
        "name", "type", "nullable", "default", "autoincrement", "length",
        "precision", "scale", "identity", "computed",
    )

    def __init__(self, col_dict: dict):
        self.name: str = col_dict["name"]
        self.type = col_dict["type"]  # The actual SQLAlchemy type *object*
        self.nullable: bool = col_dict.get("nullable", True)
        self.default = col_dict.get("default")
        self.autoincrement: bool = col_dict.get("autoincrement", False)
        # Extract length from the type object (e.g. VARCHAR(255).length = 255)
        self.length: Optional[int] = getattr(col_dict.get("type"), "length", None)
        # Precision and scale for NUMBER(p,s) / NUMERIC(p,s) enforcement
        self.precision: Optional[int] = getattr(col_dict.get("type"), "precision", None)
        self.scale: Optional[int] = getattr(col_dict.get("type"), "scale", None)
        # Identity / computed column flags (prevents writing to generated columns)
        self.identity: bool = bool(col_dict.get("identity", False))
        self.computed: bool = bool(col_dict.get("computed", False))

    @property
    def is_generated(self) -> bool:
        """True if this column is auto-managed by the DB and should be excluded from DML."""
        return self.identity or self.computed


# ---------------------------------------------------------------------------
# Dialect-aware type mapping for ADD COLUMN
# ---------------------------------------------------------------------------
_ADD_TYPE_MAP = {
    "oracle":     {"int": "NUMBER(19)", "float": "NUMBER", "bool": "NUMBER(1)", "datetime": "TIMESTAMP", "text": "VARCHAR2(255)", "clob": "CLOB"},
    "postgresql": {"int": "BIGINT", "float": "DOUBLE PRECISION", "bool": "BOOLEAN", "datetime": "TIMESTAMP WITH TIME ZONE", "text": "VARCHAR(255)", "clob": "TEXT"},
    "mysql":      {"int": "BIGINT", "float": "DOUBLE", "bool": "TINYINT", "datetime": "DATETIME", "text": "VARCHAR(255)", "clob": "LONGTEXT"},
    "mssql":      {"int": "BIGINT", "float": "FLOAT", "bool": "BIT", "datetime": "DATETIME2", "text": "VARCHAR(255)", "clob": "VARCHAR(MAX)"},
    "sqlite":     {"int": "INTEGER", "float": "REAL", "bool": "INTEGER", "datetime": "TEXT", "text": "TEXT", "clob": "TEXT"},
}


# ---------------------------------------------------------------------------
# SchemaAligner
# ---------------------------------------------------------------------------
class SchemaAligner:
    """Align a DataFrame to a SQL table schema with strict type enforcement.

    Usage::

        aligner = SchemaAligner(engine)
        df_aligned = aligner.align(df, "my_table")
    """

    # Canonical boolean literals
    TRUE_LITS  = {"1", "true", "yes", "y", "on", "t"}
    FALSE_LITS = {"0", "false", "no", "n", "off", "f"}

    def __init__(self, engine: Engine | Connection, *, config: CrudConfig | None = None):
        if engine is None:
            raise ValueError("engine is required")
        self._engine = engine
        self._cfg = config or CrudConfig()
        self._dialect = self._detect_dialect(engine)
        self._last_fingerprint = None  # populated by align() when enable_fingerprint=True

    def get_fingerprint(self):
        """Return the schema fingerprint from the last align() call, or None."""
        return self._last_fingerprint

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def align(
        self,
        df: pd.DataFrame,
        table: str,
        schema: str | None = None,
    ) -> pd.DataFrame:
        """Main entry — align *df* to match the schema of *table*."""
        inspector = sa.inspect(self._engine)

        if not inspector.has_table(table, schema=schema):
            raise NoSuchTableError(f"Table '{table}' not found")

        # 1. Introspect
        meta = {c["name"]: ColumnInfo(c) for c in inspector.get_columns(table, schema=schema)}

        # 1b. Schema fingerprint (for drift detection)
        if self._cfg.enable_fingerprint:
            self._last_fingerprint = build_fingerprint(inspector, table, schema)
            logger.info(
                "Schema fingerprint for '%s': %s (%d columns)",
                table, self._last_fingerprint.hash, len(self._last_fingerprint.columns),
            )

        # 2. Column name alignment (case-insensitive + BOM strip)
        df = self._align_column_names(df, meta)

        # 3. Schema evolution: add missing columns if enabled
        if self._cfg.add_missing_cols:
            new_cols = [c for c in df.columns if c not in meta]
            if new_cols:
                logger.info("Schema evolution: adding %d columns: %s", len(new_cols), new_cols)
                self._add_missing_columns(table, schema, df, new_cols)
                # Re-introspect after DDL
                meta = {c["name"]: ColumnInfo(c) for c in inspector.get_columns(table, schema=schema)}

        # 4a. Drop identity / computed columns — never include in DML binds
        generated = [name for name, info in meta.items() if info.is_generated]
        if generated:
            logger.info("Excluding generated columns from DML: %s", generated)
            df = df[[c for c in df.columns if c not in generated]]

        # 4b. Drop extra columns (or raise, per config)
        extra = [c for c in df.columns if c not in meta]
        if extra:
            if not self._cfg.drop_extra_cols:
                raise ValueError(
                    f"Extra columns found in data but drop_extra_cols=False: {extra}. "
                    f"Set CrudConfig.drop_extra_cols=True or remove them from input."
                )
            logger.warning("Dropping extra columns not in target: %s", extra)
            df = df[[c for c in df.columns if c in meta]]

        # 5. Type coercion
        df = self._coerce_types(df, meta)

        # 6. Nullability enforcement
        df = self._enforce_nullability(df, meta)

        # 7. Reorder to DB column order, add missing required columns as None
        #    Skip generated columns — they must not appear in bind params
        final_cols = []
        for col_name, info in meta.items():
            if info.is_generated:
                continue
            if col_name in df.columns:
                final_cols.append(col_name)
            else:
                if not info.nullable and info.default is None:
                    logger.warning("Missing required column '%s' — filling with NULL", col_name)
                df[col_name] = None
                final_cols.append(col_name)

        # 8. Finalize types for driver compatibility
        df = self._finalize_types(df)

        return df[final_cols]

    # -------------------------------------------------------------------------
    # Column name alignment (§B.6–10 from SchemaAlign.txt)
    # -------------------------------------------------------------------------
    def _align_column_names(self, df: pd.DataFrame, meta: Dict[str, ColumnInfo]) -> pd.DataFrame:
        """Case-insensitive matching + BOM strip."""
        db_map = {k.lower(): k for k in meta}
        rename = {}
        for col in df.columns:
            clean = str(col).lstrip("\ufeff").strip()
            low = clean.lower()
            if low in db_map and clean != db_map[low]:
                rename[col] = db_map[low]
            elif clean != col:
                rename[col] = clean
        if rename:
            logger.info("Column rename map: %s", rename)
            df = df.rename(columns=rename)
        return df

    # -------------------------------------------------------------------------
    # Schema evolution: ALTER TABLE ADD COLUMN
    # -------------------------------------------------------------------------
    def _add_missing_columns(self, table: str, schema: str | None, df: pd.DataFrame, new_cols: List[str]):
        """Generate and execute ALTER TABLE ADD for missing columns."""
        types = _ADD_TYPE_MAP.get(self._dialect, _ADD_TYPE_MAP["postgresql"])
        full_table = f"{schema}.{table}" if schema else table

        def _map_type(s: pd.Series) -> str:
            if pd.api.types.is_integer_dtype(s):
                return types["int"]
            if pd.api.types.is_float_dtype(s):
                return types["float"]
            if pd.api.types.is_bool_dtype(s):
                return types["bool"]
            if pd.api.types.is_datetime64_any_dtype(s):
                return types["datetime"]
            # String — check max length
            non_null = s.dropna()
            if len(non_null) > 0:
                max_len = int(non_null.astype(str).str.len().max())
                if max_len > 4000:
                    return types["clob"]
                if max_len > 255:
                    if self._dialect == "oracle":
                        return f"VARCHAR2({max_len})"
                    return f"VARCHAR({max_len})"
            return types["text"]

        def _quote(name: str) -> str:
            if self._dialect in ("mysql", "mariadb"):
                return f"`{name}`"
            if self._dialect == "mssql":
                return f"[{name}]"
            return f'"{name}"'

        def _run(sql: str):
            if isinstance(self._engine, Engine):
                with self._engine.begin() as c:
                    c.execute(text(sql))
            else:
                self._engine.execute(text(sql))

        for col in new_cols:
            try:
                sql_type = _map_type(df[col])
                q_col = _quote(col)
                # MSSQL and Oracle: ADD col_name type (no COLUMN keyword)
                if self._dialect in ("mssql", "oracle"):
                    stmt = f"ALTER TABLE {full_table} ADD {q_col} {sql_type}"
                else:
                    stmt = f"ALTER TABLE {full_table} ADD COLUMN {q_col} {sql_type}"
                logger.info("Executing: %s", stmt)
                _run(stmt)
            except (SQLAlchemyError, ValueError) as exc:
                logger.error("Failed to add column '%s': %s", col, exc)

    # -------------------------------------------------------------------------
    # Type coercion — the CRITICAL fix: dispatch on type *objects*
    # -------------------------------------------------------------------------
    def _coerce_types(self, df: pd.DataFrame, meta: Dict[str, ColumnInfo]) -> pd.DataFrame:
        """Coerce each column to match its SQL type."""
        out = df.copy()
        on_err = self._cfg.on_error
        thresh = self._cfg.failure_threshold

        for col_name, info in meta.items():
            if col_name not in out.columns:
                continue
            s = out[col_name]
            if s.empty:
                continue

            # Dispatch on the actual SQLAlchemy type OBJECT (not string)
            col_type = info.type

            if self._is_int_type(col_type):
                out[col_name] = self._coerce_int(s, info, on_err, thresh)
            elif self._is_bool_type(col_type):
                out[col_name] = self._coerce_bool(s, info, on_err, thresh)
            elif self._is_float_type(col_type):
                out[col_name] = self._coerce_float(s, info, on_err, thresh)
            elif self._is_datetime_type(col_type):
                out[col_name] = self._coerce_datetime(s, info, on_err, thresh)
            elif self._is_string_type(col_type):
                out[col_name] = self._coerce_string(s, info, on_err, thresh)
            elif self._is_json_type(col_type):
                out[col_name] = self._coerce_json(s, info, on_err, thresh)
            elif self._is_binary_type(col_type):
                out[col_name] = self._coerce_binary(s, info, on_err, thresh)

        return out

    # ---- coercion handlers ----

    def _coerce_int(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        num = pd.to_numeric(s, errors="coerce")
        bad_str = s.notna() & num.isna()
        has_frac = num.notna() & ((num.fillna(0) % 1).abs() > 1e-9)
        failures = bad_str | has_frac
        if failures.any():
            self._check_failure_rate(failures, info.name, on_err, thresh, "integer coercion")
            num[failures] = np.nan
        return num.astype("Int64")

    def _coerce_float(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        num = pd.to_numeric(s, errors="coerce")
        failures = s.notna() & num.isna()
        if failures.any():
            self._check_failure_rate(failures, info.name, on_err, thresh, "numeric coercion")

        # Precision / scale enforcement for NUMBER(p,s) / NUMERIC(p,s)
        if info.scale is not None and info.scale > 0:
            num = num.round(info.scale)
            logger.debug("[%s] Rounded to scale=%d", info.name, info.scale)

        if info.precision is not None and info.scale is not None:
            max_integer_digits = info.precision - info.scale
            if max_integer_digits > 0:
                abs_int_part = num.dropna().apply(lambda v: len(str(int(abs(v)))))
                overflow = abs_int_part > max_integer_digits
                if overflow.any():
                    overflow_idx = overflow[overflow].index
                    overflow_full = pd.Series(False, index=num.index)
                    overflow_full.loc[overflow_idx] = True
                    self._check_failure_rate(
                        overflow_full, info.name, on_err, thresh,
                        f"precision overflow (max {max_integer_digits} integer digits for NUMBER({info.precision},{info.scale}))",
                    )
                    num.loc[overflow_idx] = np.nan

        return num

    def _coerce_string(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        out = s.astype(str).where(s.notna())
        if info.length:
            if self._cfg.byte_semantics:
                # BYTE semantics: measure length in encoded bytes (UTF-8)
                byte_lens = out.dropna().apply(lambda v: len(v.encode("utf-8")))
                over_idx = byte_lens[byte_lens > info.length].index
                over = pd.Series(False, index=out.index)
                over.loc[over_idx] = True
                label = f"max byte-length {info.length}"
            else:
                # CHAR semantics: measure length in characters (default)
                over = out.str.len() > info.length
                label = f"max char-length {info.length}"
            if over.any():
                self._check_failure_rate(over, info.name, on_err, thresh, label)
                out.loc[over] = None
        return out

    def _coerce_bool(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        s_norm = s.astype(str).str.lower().str.strip()
        s_norm = s_norm.where(s.notna(), np.nan)
        mask_t = s_norm.isin(self.TRUE_LITS)
        mask_f = s_norm.isin(self.FALSE_LITS)
        failures = s.notna() & ~mask_t & ~mask_f
        if failures.any():
            self._check_failure_rate(failures, info.name, on_err, thresh, "boolean coercion")
        out = pd.Series(index=s.index, dtype="object")
        out[mask_t] = True
        out[mask_f] = False
        out[failures] = None
        return out.astype("boolean")

    def _coerce_datetime(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        try:
            dt = pd.to_datetime(s, errors="coerce")
        except (TypeError, ValueError):
            dt = pd.Series(pd.NaT, index=s.index)

        # Fallback: dateutil per-value parsing for entries that failed
        if _du is not None:
            retry = dt.isna() & s.notna()
            if retry.any():
                def _try(v):
                    try:
                        return _du.parse(str(v))
                    except Exception:
                        return pd.NaT
                dt.loc[retry] = pd.to_datetime(s.loc[retry].map(_try), errors="coerce")

        failures = s.notna() & dt.isna()
        if failures.any():
            self._check_failure_rate(failures, info.name, on_err, thresh, "datetime parsing")
        return dt

    def _coerce_json(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        import json as _json

        def _validate(val):
            if pd.isna(val):
                return None
            if isinstance(val, (dict, list)):
                return _json.dumps(val)
            try:
                return _json.dumps(_json.loads(str(val)))
            except (ValueError, TypeError):
                return None

        res = s.apply(_validate)
        failures = s.notna() & res.isna()
        if failures.any():
            self._check_failure_rate(failures, info.name, on_err, thresh, "JSON validation")
        return res

    def _coerce_binary(self, s: pd.Series, info: ColumnInfo, on_err: str, thresh: float) -> pd.Series:
        valid = s.map(lambda x: pd.isna(x) or isinstance(x, (bytes, bytearray, memoryview)))
        failures = ~valid
        if failures.any():
            self._check_failure_rate(failures, info.name, on_err, thresh, "binary content")
        out = s.copy()
        out[failures] = None
        return out

    # ---- failure rate check ----

    def _check_failure_rate(self, mask: pd.Series, col: str, on_err: str, thresh: float, label: str):
        count = int(mask.sum())
        if count == 0:
            return
        total = len(mask)
        rate = count / total if total > 0 else 0.0

        if rate <= thresh:
            logger.warning("[%s] %d/%d (%.1f%%) failures ≤ threshold — coercing to NULL", col, count, total, rate * 100)
            return

        msg = f"[{col}] {count}/{total} ({rate:.1%}) rows failed {label}. Threshold={thresh:.1%}."
        if on_err == "coerce":
            logger.error("%s Coercing to NULL (on_error='coerce').", msg)
            return
        raise ValueError(f"{msg} Aborting.")

    # -------------------------------------------------------------------------
    # Nullability enforcement (§E.19)
    # -------------------------------------------------------------------------
    def _enforce_nullability(self, df: pd.DataFrame, meta: Dict[str, ColumnInfo]) -> pd.DataFrame:
        on_err = self._cfg.on_error
        thresh = self._cfg.failure_threshold
        for col_name, info in meta.items():
            if col_name not in df.columns:
                continue
            if not info.nullable:
                nulls = df[col_name].isna()
                if nulls.any():
                    self._check_failure_rate(nulls, col_name, on_err, thresh, "NOT NULL constraint")
        return df

    # -------------------------------------------------------------------------
    # Finalize types — convert pandas extension types to native Python
    # Prevents Oracle DPY-3002 and similar driver errors.
    # -------------------------------------------------------------------------
    def _finalize_types(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in out.columns:
            dtype = out[col].dtype
            if pd.api.types.is_integer_dtype(dtype):
                out[col] = out[col].astype(object).where(out[col].notna(), None)
            elif pd.api.types.is_float_dtype(dtype):
                out[col] = out[col].astype(object).where(out[col].notna(), None)
            elif pd.api.types.is_bool_dtype(dtype):
                out[col] = out[col].astype(object).where(out[col].notna(), None)
        return out

    # -------------------------------------------------------------------------
    # Type classification — dispatch on SQLAlchemy type OBJECTS
    # -------------------------------------------------------------------------
    def _is_int_type(self, t: Any) -> bool:
        if isinstance(t, (sat.INTEGER, sat.BIGINT, sat.SmallInteger)):
            return True
        if _ORA and isinstance(t, NUMBER) and getattr(t, "scale", None) == 0:
            return True
        if _MY and isinstance(t, (TINYINT, MY_SMALLINT, MEDIUMINT, MY_INT, MY_BIGINT)):
            return True
        if _MS and isinstance(t, (MS_TINYINT, MS_SMALLINT, MS_INT, MS_BIGINT)):
            return True
        return False

    def _is_float_type(self, t: Any) -> bool:
        if isinstance(t, (sat.Float, sat.REAL, sat.DOUBLE_PRECISION, sat.Numeric)):
            return True
        if _ORA:
            if isinstance(t, (ORA_FLOAT, BINARY_DOUBLE, BINARY_FLOAT)):
                return True
            if isinstance(t, NUMBER) and getattr(t, "scale", None) and t.scale > 0:
                return True
        if _MY and isinstance(t, (MY_FLOAT, MY_DOUBLE, MY_DECIMAL)):
            return True
        if _MS and isinstance(t, (MS_FLOAT, MS_REAL, MS_DECIMAL, MONEY, SMALLMONEY)):
            return True
        return False

    def _is_string_type(self, t: Any) -> bool:
        if isinstance(t, (sat.String, sat.Unicode, sat.Text, sat.VARCHAR, sat.CHAR)):
            return True
        if _ORA and isinstance(t, (VARCHAR2, NVARCHAR2, ORA_CHAR, NCHAR, CLOB, NCLOB, LONG)):
            return True
        if _MY and isinstance(t, (MY_VARCHAR, MY_CHAR, MY_TEXT, TINYTEXT, MEDIUMTEXT, LONGTEXT, MY_ENUM, MY_SET)):
            return True
        if _MS and isinstance(t, (MS_VARCHAR, MS_CHAR, NVARCHAR, MS_NCHAR, MS_TEXT, NTEXT)):
            return True
        return False

    def _is_bool_type(self, t: Any) -> bool:
        if isinstance(t, sat.Boolean):
            return True
        # Oracle NUMBER(1)
        if _ORA and isinstance(t, NUMBER):
            if getattr(t, "precision", None) == 1 and not getattr(t, "scale", None):
                return True
        # MySQL TINYINT(1)
        if _MY and isinstance(t, TINYINT):
            if getattr(t, "display_width", None) == 1:
                return True
        # MSSQL BIT
        if _MS and isinstance(t, BIT):
            return True
        return False

    def _is_datetime_type(self, t: Any) -> bool:
        if isinstance(t, (sat.DateTime, sat.TIMESTAMP, sat.Date)):
            return True
        if _ORA and isinstance(t, (ORA_DATE, ORA_TIMESTAMP, INTERVAL)):
            return True
        if _MY and isinstance(t, (MY_DATETIME, MY_TIMESTAMP, MY_DATE, MY_TIME)):
            return True
        if _MS and isinstance(t, (MS_DATETIME, DATETIME2, SMALLDATETIME, MS_DATE, MS_TIME, DATETIMEOFFSET)):
            return True
        return False

    def _is_json_type(self, t: Any) -> bool:
        if isinstance(t, (sat.JSON, sat.ARRAY)):
            return True
        if type(t).__name__.upper() in ("JSON", "JSONB"):
            return True
        if _MY and isinstance(t, MY_JSON):
            return True
        return False

    def _is_binary_type(self, t: Any) -> bool:
        if isinstance(t, (sat.LargeBinary, sat.BINARY, sat.VARBINARY, sat.BLOB)):
            return True
        if _ORA and isinstance(t, (RAW, ORA_BLOB)):
            return True
        if _MY and isinstance(t, (MY_BLOB, TINYBLOB, MEDIUMBLOB, LONGBLOB)):
            return True
        if _MS and isinstance(t, (MS_BINARY, MS_VARBINARY, IMAGE)):
            return True
        return False

    # -------------------------------------------------------------------------
    # Dialect detection
    # -------------------------------------------------------------------------
    @staticmethod
    def _detect_dialect(engine: Engine | Connection) -> str:
        if isinstance(engine, Connection):
            name = engine.engine.dialect.name.lower()
        else:
            name = engine.dialect.name.lower()
        if name in ("postgresql", "postgres"):
            return "postgresql"
        if name == "mariadb":
            return "mysql"
        return name
