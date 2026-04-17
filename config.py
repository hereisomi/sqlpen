from __future__ import annotations
"""
Pipeline configuration — loads settings from config.yml and maps them
to CrudConfig for the pipeline runner.

Resolution order: explicit kwargs > config.yml > built-in defaults.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from crud.types import CrudConfig

logger = logging.getLogger(__name__)


def get_engine_from_env(url: Optional[str] = None) -> Engine:
    """Resolve a SQLAlchemy Engine from an explicit URL, env var, or .env file.

    Resolution order:
    1. Explicit ``url`` argument
    2. ``DATABASE_URL`` environment variable
    3. ``DATABASE_URL`` key inside a ``.env`` file in CWD or project root

    Raises ``ValueError`` if no connection string can be found.
    """
    if url:
        return sa.create_engine(url)

    # Check OS environment
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        logger.info("Engine resolved from DATABASE_URL environment variable.")
        return sa.create_engine(env_url)

    # Attempt .env file discovery
    for candidate in [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]:
        if candidate.exists():
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    if key.strip() == "DATABASE_URL":
                        val = val.strip().strip('"').strip("'")
                        logger.info("Engine resolved from .env file: %s", candidate)
                        return sa.create_engine(val)
            except Exception as exc:
                logger.warning("Failed reading .env at %s: %s", candidate, exc)

    raise ValueError(
        "No database connection found. Provide a URL, set DATABASE_URL "
        "environment variable, or create a .env file with DATABASE_URL=..."
    )

# Default config.yml search locations
_SEARCH_PATHS = [
    Path.cwd() / "config.yml",
]

# Try project root (one level above this file)
try:
    _SEARCH_PATHS.append(Path(__file__).resolve().parents[1] / "config.yml")
except Exception:
    pass


def _find_config() -> Optional[Path]:
    """Find the first config.yml that exists."""
    for p in _SEARCH_PATHS:
        if p.exists():
            return p
    return None


def load_pipeline_config(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Load the full pipeline + schema_corrector config sections.

    Returns a merged dict with keys from both sections.
    """
    config_path = Path(path) if path else _find_config()

    defaults = {
        "outlier_pct": 0.5,
        "casting": True,
        "cleaner": True,
        "profiler": True,
        "trace_sql": True,
        "schema_save_path": "schema",
        "chunk_size": 10_000,
        # From schema_corrector section
        "on_error": "coerce",
        "failure_threshold": 0.03,
        "validate_fk": False,
        "add_missing_cols": False,
        "byte_semantics": False,
        "drop_extra_cols": True,
        "enable_fingerprint": True,
    }

    if config_path and config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            # Merge pipeline section
            pipeline = raw.get("pipeline", {})
            if isinstance(pipeline, dict):
                for k, v in pipeline.items():
                    if k in defaults:
                        defaults[k] = v

            # Merge schema_corrector section
            corrector = raw.get("schema_corrector", {})
            if isinstance(corrector, dict):
                for k, v in corrector.items():
                    if k in defaults:
                        defaults[k] = v

            logger.info("Loaded pipeline config from %s", config_path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s (using defaults)", config_path, exc)
    else:
        logger.info("No config.yml found; using defaults")

    return defaults


def build_crud_config(
    overrides: Optional[Dict[str, Any]] = None,
    config_path: Optional[str | Path] = None,
) -> CrudConfig:
    """Build a CrudConfig from config.yml + optional overrides.

    Parameters
    ----------
    overrides : dict, optional
        Explicit overrides (highest priority).
    config_path : str or Path, optional
        Path to config.yml.  If None, auto-discovered.
    """
    merged = load_pipeline_config(config_path)

    if overrides:
        merged.update(overrides)

    # Map config.yml keys to CrudConfig field names
    failure_thresh = merged.get("failure_threshold", 0.03)
    if isinstance(failure_thresh, (int, float)) and failure_thresh > 1:
        # config.yml uses percentage (e.g. 3.0), CrudConfig uses fraction (0.03)
        failure_thresh = failure_thresh / 100.0

    return CrudConfig(
        chunk_size=int(merged.get("chunk_size", 10_000)),
        strict=False,
        add_missing_cols=bool(merged.get("add_missing_cols", False)),
        trace_sql=bool(merged.get("trace_sql", False)),
        on_error=str(merged.get("on_error", "coerce")),
        failure_threshold=float(failure_thresh),
        byte_semantics=bool(merged.get("byte_semantics", False)),
        drop_extra_cols=bool(merged.get("drop_extra_cols", True)),
        enable_fingerprint=bool(merged.get("enable_fingerprint", True)),
    )


"""
Table setup — create or verify the target table before pipeline execution.

Handles:
- Auto-creating tables from DataFrame schema (DDL generation)
- Verifying existing tables are compatible
- Schema evolution for existing tables
"""

import logging
from typing import Optional

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def ensure_table(
    engine: Engine,
    df: pd.DataFrame,
    table: str,
    schema: Optional[str] = None,
    pk_cols: Optional[list[str]] = None,
    if_exists: str = "skip",
) -> bool:
    """Ensure *table* exists, creating it from *df* schema if needed.

    Parameters
    ----------
    engine : Engine
    df : pd.DataFrame
        Used to infer column types and generate DDL.
    table : str
    schema : str, optional
    pk_cols : list[str], optional
        Columns to set as PRIMARY KEY.
    if_exists : str
        ``"skip"`` (default): do nothing if table exists.
        ``"drop"``: drop and recreate.
        ``"fail"``: raise if table already exists.
        ``"alter"``: add missing columns & widen VARCHAR non-destructively.

    Returns
    -------
    bool
        True if the table was created/altered, False if skipped.
    """
    inspector = sa.inspect(engine)
    exists = inspector.has_table(table, schema=schema)

    if exists:
        if if_exists == "fail":
            raise ValueError(f"Table '{table}' already exists (if_exists='fail')")
        if if_exists == "drop":
            _drop_table(engine, table, schema)
            logger.info("Dropped existing table '%s'", table)
        elif if_exists == "alter":
            return _evolve_schema(engine, inspector, df, table, schema)
        else:
            logger.info("Table '%s' already exists — skipping creation", table)
            return False

    # Build DDL from DataFrame dtypes
    ddl = _build_create_table(engine, df, table, schema, pk_cols)
    logger.info("Creating table '%s':\n%s", table, ddl)

    with engine.begin() as conn:
        conn.execute(text(ddl))

    return True


def _evolve_schema(
    engine: Engine,
    inspector,
    df: pd.DataFrame,
    table: str,
    schema: Optional[str],
) -> bool:
    """Non-destructive schema evolution: ADD COLUMN / widen VARCHAR."""
    dialect = engine.dialect.name.lower()
    existing_cols = {c["name"].lower(): c for c in inspector.get_columns(table, schema=schema)}
    df_cols = set(df.columns)
    altered = False

    full_table = _quote_ident(table, dialect)
    if schema:
        full_table = f"{_quote_ident(schema, dialect)}.{full_table}"

    stmts = []
    for col_name in df_cols:
        col_lower = col_name.lower()
        sql_type = _pandas_to_sql_type(df[col_name], dialect)

        if col_lower not in existing_cols:
            # New column — ADD it
            col_ident = _quote_ident(col_name, dialect)
            stmts.append(f"ALTER TABLE {full_table} ADD {col_ident} {sql_type} NULL")
            altered = True
        else:
            # Existing column — check if VARCHAR needs widening
            db_col = existing_cols[col_lower]
            db_type = str(db_col.get("type", "")).upper()
            if "VARCHAR" in db_type or "VARCHAR2" in db_type:
                db_len = getattr(db_col["type"], "length", None) or 0
                non_null = df[col_name].dropna()
                if len(non_null) > 0:
                    needed = int(non_null.astype(str).str.len().max())
                    if needed > db_len:
                        col_ident = _quote_ident(col_name, dialect)
                        new_type = _varchar(dialect, needed)
                        if _norm_dialect(dialect) == "oracle":
                            stmts.append(f"ALTER TABLE {full_table} MODIFY {col_ident} {new_type}")
                        else:
                            stmts.append(f"ALTER TABLE {full_table} ALTER COLUMN {col_ident} TYPE {new_type}")
                        altered = True

    if stmts:
        with engine.begin() as conn:
            for stmt in stmts:
                logger.info("Schema evolution: %s", stmt)
                conn.execute(text(stmt))

    return altered


def _build_create_table(
    engine: Engine,
    df: pd.DataFrame,
    table: str,
    schema: Optional[str],
    pk_cols: Optional[list[str]],
) -> str:
    """Generate CREATE TABLE DDL from DataFrame dtypes."""
    dialect = engine.dialect.name.lower()

    col_defs = []
    for col_name in df.columns:
        sql_type = _pandas_to_sql_type(df[col_name], dialect)
        nullable = "NOT NULL" if pk_cols and col_name in pk_cols else "NULL"
        col_ident = _quote_ident(col_name, dialect)
        col_defs.append(f"    {col_ident} {sql_type} {nullable}")

    if pk_cols:
        pk_idents = ", ".join(_quote_ident(c, dialect) for c in pk_cols)
        col_defs.append(f"    PRIMARY KEY ({pk_idents})")

    full_table = _quote_ident(table, dialect)
    if schema:
        full_table = f"{_quote_ident(schema, dialect)}.{full_table}"

    body = ",\n".join(col_defs)
    return f"CREATE TABLE {full_table} (\n{body}\n)"


def _pandas_to_sql_type(series: pd.Series, dialect: str) -> str:
    """Map a pandas Series dtype to a SQL type string."""
    dtype = series.dtype

    if pd.api.types.is_integer_dtype(dtype):
        return _type_map(dialect, "int")
    if pd.api.types.is_float_dtype(dtype):
        return _type_map(dialect, "float")
    if pd.api.types.is_bool_dtype(dtype):
        return _type_map(dialect, "bool")
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return _type_map(dialect, "datetime")

    # String — check max length
    non_null = series.dropna()
    if len(non_null) > 0:
        max_len = int(non_null.astype(str).str.len().max())
        if max_len > 4000:
            return _type_map(dialect, "clob")
        if max_len > 255:
            return _varchar(dialect, max_len)

    return _type_map(dialect, "text")


_TYPE_MAPS = {
    "oracle":     {"int": "NUMBER(19)",    "float": "NUMBER",          "bool": "NUMBER(1)", "datetime": "TIMESTAMP", "text": "VARCHAR2(255)", "clob": "CLOB"},
    "postgresql": {"int": "BIGINT",        "float": "DOUBLE PRECISION","bool": "BOOLEAN",   "datetime": "TIMESTAMP", "text": "VARCHAR(255)",  "clob": "TEXT"},
    "mysql":      {"int": "BIGINT",        "float": "DOUBLE",          "bool": "TINYINT(1)","datetime": "DATETIME",  "text": "VARCHAR(255)",  "clob": "LONGTEXT"},
    "mssql":      {"int": "BIGINT",        "float": "FLOAT",           "bool": "BIT",       "datetime": "DATETIME2", "text": "VARCHAR(255)",  "clob": "VARCHAR(MAX)"},
    "sqlite":     {"int": "INTEGER",       "float": "REAL",            "bool": "INTEGER",   "datetime": "TEXT",      "text": "TEXT",           "clob": "TEXT"},
}


def _type_map(dialect: str, kind: str) -> str:
    d = _norm_dialect(dialect)
    return _TYPE_MAPS.get(d, _TYPE_MAPS["postgresql"])[kind]


def _varchar(dialect: str, length: int) -> str:
    d = _norm_dialect(dialect)
    if d == "oracle":
        return f"VARCHAR2({length})"
    return f"VARCHAR({length})"


def _norm_dialect(dialect: str) -> str:
    d = dialect.lower()
    if d in ("postgres", "postgresql"):
        return "postgresql"
    if d == "mariadb":
        return "mysql"
    if "mssql" in d or "sqlserver" in d:
        return "mssql"
    return d


def _quote_ident(name: str, dialect: str) -> str:
    d = _norm_dialect(dialect)
    if d in ("mysql", "mariadb"):
        return f"`{name}`"
    if d == "mssql":
        return f"[{name}]"
    return f'"{name}"'


def _drop_table(engine: Engine, table: str, schema: Optional[str]) -> None:
    dialect = engine.dialect.name.lower()
    full = _quote_ident(table, dialect)
    if schema:
        full = f"{_quote_ident(schema, dialect)}.{full}"

    if "oracle" in dialect:
        ddl = f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {full}'; EXCEPTION WHEN OTHERS THEN NULL; END;"
    else:
        ddl = f"DROP TABLE IF EXISTS {full}"

    with engine.begin() as conn:
        conn.execute(text(ddl))
