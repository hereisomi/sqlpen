"""
peek.py — Quick database introspection and query execution.

A lightweight, standalone module for:
- Listing tables, schemas, views
- Describing table schemas and constraints
- Executing arbitrary SQL queries
- Analyzing DataFrames against table schemas
- Aligning and correcting DataFrame types to match SQL schema
- Table activity and timestamp detection

Uses SchemaInspector, SchemaAnalyzer, SchemaManager, SchemaAligner,
and df_align_to_sql from misc/ for robust introspection.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Core utilities
from misc.schema_inspector import SchemaInspector
from misc.schema_analyzer import (
    SchemaAnalyzer,
    analyze_table as _analyze_table_fn,
    TableAnalysisReport,
    ColumnInfo,
    ConstraintInfo,
    MappingInfo,
    ValidationSummary,
    DialectChecks,
    EngineInfo,
)
from misc.schema_manager import SchemaManager
from misc.schema_corrector import SchemaAligner
from misc.df_align_to_sql import (
    align_dataframe_to_schema,
    normalize_sql_type,
    detect_outliers,
    correct_outliers,
    generate_schema,
)
from config import get_engine_from_env
from misc.engine_manager import _Registry as _EngineRegistry

# Singleton inspector instance
_inspector = SchemaInspector()


def get_engine(url: Optional[str] = None) -> Engine:
    """
    Resolve a SQLAlchemy Engine.

    Uses a pooled engine registry — the same URL always returns the same
    engine instance, avoiding redundant connection pool creation across
    repeated peek calls.

    Resolution order:
    1. Explicit url argument
    2. DATABASE_URL environment variable
    3. .env file in CWD or project root

    Parameters
    ----------
    url : str, optional
        Database connection string. If None, auto-resolved.

    Returns
    -------
    Engine
        SQLAlchemy engine instance
    """
    # Resolve the URL string first (handles env var / .env fallback)
    resolved_engine = get_engine_from_env(url)
    resolved_url = str(resolved_engine.url)

    # Return a pooled engine for the resolved URL
    # pool_pre_ping=True ensures stale connections are detected and recycled
    return _EngineRegistry.get(resolved_url, pool_pre_ping=True)


def tables(url: Optional[str] = None, schema: Optional[str] = None) -> List[str]:
    """
    List all tables in the database.
    
    Parameters
    ----------
    url : str, optional
        Database URL. Uses get_engine() resolution if not provided.
    schema : str, optional
        Database schema/namespace (for multi-schema databases like Postgres, MSSQL)
        
    Returns
    -------
    List[str]
        List of table names
        
    Examples
    --------
    >>> import peek as pk
    >>> pk.tables("sqlite:///my.db")
    ['users', 'orders', 'products']
    """
    engine = get_engine(url)
    return _inspector.get_table_names(engine, schema=schema)


def describe(
    table: str, 
    url: Optional[str] = None, 
    schema: Optional[str] = None
) -> pd.DataFrame:
    """
    Describe table schema (columns, types, constraints).
    
    Parameters
    ----------
    table : str
        Table name to describe
    url : str, optional
        Database URL. Uses get_engine() resolution if not provided.
    schema : str, optional
        Database schema/namespace
        
    Returns
    -------
    pd.DataFrame
        DataFrame with columns: name, type, nullable, default, autoincrement
        
    Examples
    --------
    >>> import peek as pk
    >>> pk.describe("users", "postgresql://localhost/db")
       name        type  nullable             default autoincrement
    0    id     INTEGER     False  nextval('users_id_seq')        True
    1  email   VARCHAR(255)  True                  None       False
    """
    engine = get_engine(url)
    columns = _inspector.get_columns(engine, table, schema=schema)
    
    if not columns:
        raise ValueError(f"Table '{table}' not found or has no columns")
    
    # Normalize column metadata to DataFrame
    records = []
    for col in columns:
        records.append({
            'name': col.get('name'),
            'type': str(col.get('type')),
            'nullable': col.get('nullable', True),
            'default': col.get('default'),
            'autoincrement': col.get('autoincrement', False),
        })
    
    return pd.DataFrame(records)


def describe_full(
    table: str,
    url: Optional[str] = None,
    schema: Optional[str] = None
) -> Dict[str, Any]:
    """
    Full table description including columns, PKs, unique constraints, identity cols.
    
    Parameters
    ----------
    table : str
        Table name
    url : str, optional
        Database URL
    schema : str, optional
        Database schema
        
    Returns
    -------
    Dict[str, Any]
        Complete table metadata dictionary
    """
    engine = get_engine(url)
    
    return {
        'table': table,
        'schema': schema,
        'columns': _inspector.get_columns(engine, table, schema),
        'primary_keys': _inspector.get_primary_keys(engine, table, schema),
        'unique_constraints': _inspector.get_unique_constraints(engine, table, schema),
        'identity_columns': list(_inspector.get_identity_columns(engine, table, schema)),
    }


def query(
    sql: str,
    url: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    **kwargs
) -> pd.DataFrame:
    """
    Execute SQL query and return results as DataFrame.
    
    Parameters
    ----------
    sql : str
        SQL query string. Can use :param style placeholders.
    url : str, optional
        Database URL
    params : dict, optional
        Parameters for SQL bind variables
    **kwargs
        Additional arguments passed to pd.read_sql()
        
    Returns
    -------
    pd.DataFrame
        Query results
        
    Examples
    --------
    >>> import peek as pk
    >>> df = pk.query("SELECT * FROM users WHERE age > :min_age", 
    ...               params={'min_age': 18})
    """
    engine = get_engine(url)
    params = params or {}
    
    logger.debug("Executing query: %s | params=%s", sql, params)
    
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(sql), conn, params=params, **kwargs)
    
    return df


def query_clean(
    sql: str,
    url: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    clean_columns: bool = True,
    **kwargs
) -> pd.DataFrame:
    """
    Execute query with optional column name cleaning.
    
    Sanitizes DataFrame column names after retrieval to match
    SqlPen's internal conventions (lowercase, no spaces/special chars).
    
    Parameters
    ----------
    sql : str
        SQL query string
    url : str, optional
        Database URL
    params : dict, optional
        Query parameters
    clean_columns : bool, default True
        Whether to sanitize column names (User ID! -> user_id)
    **kwargs
        Additional arguments for pd.read_sql()
        
    Returns
    -------
    pd.DataFrame
        Query results with optionally cleaned column names
    """
    from utils.ddl import sanitize_dataframe_columns
    
    df = query(sql, url=url, params=params, **kwargs)
    
    if clean_columns and not df.empty:
        engine = get_engine(url)
        dialect = engine.dialect.name.lower()
        df, _ = sanitize_dataframe_columns(
            df, server=dialect, allow_space=False, to_lower=True
        )
    
    return df


def has_table(table: str, url: Optional[str] = None, schema: Optional[str] = None) -> bool:
    """
    Check if table exists.
    
    Parameters
    ----------
    table : str
        Table name to check
    url : str, optional
        Database URL
    schema : str, optional
        Database schema
        
    Returns
    -------
    bool
        True if table exists
    """
    engine = get_engine(url)
    return _inspector.has_table(engine, table, schema=schema)


def get_pk(table: str, url: Optional[str] = None, schema: Optional[str] = None) -> List[str]:
    """
    Get primary key columns for a table.
    
    Parameters
    ----------
    table : str
        Table name
    url : str, optional
        Database URL
    schema : str, optional
        Database schema
        
    Returns
    -------
    List[str]
        Primary key column names
    """
    engine = get_engine(url)
    return _inspector.get_primary_keys(engine, table, schema=schema)


def validate_upsert(
    table: str,
    key_cols: List[str],
    url: Optional[str] = None,
    schema: Optional[str] = None
) -> None:
    """
    Validate that table has PK or UNIQUE constraint on key columns for upsert.
    
    Parameters
    ----------
    table : str
        Table name
    key_cols : List[str]
        Columns to use for upsert matching
    url : str, optional
        Database URL
    schema : str, optional
        Database schema
        
    Raises
    ------
    ValueError
        If no suitable constraint found for upsert
    """
    engine = get_engine(url)
    _inspector.validate_upsert_constraints(engine, table, key_cols, schema=schema)


# ---------------------------------------------------------------------------
# SchemaManager — rich read-only introspection
# ---------------------------------------------------------------------------

def get_manager(url: Optional[str] = None) -> SchemaManager:
    """
    Return a SchemaManager bound to the resolved engine.

    SchemaManager exposes richer read-only introspection than peek's
    stateless functions: list_schemas, list_views, find_column,
    get_table_details, resolve_table, detect_timestamp_columns,
    table_activity_status, classify_table_activity, compare_to_structure,
    tail.

    Parameters
    ----------
    url : str, optional
        Database URL. Uses get_engine() resolution if not provided.

    Returns
    -------
    SchemaManager

    Examples
    --------
    >>> import peek as pk
    >>> mgr = pk.get_manager("postgresql://localhost/mydb")
    >>> mgr.list_schemas()
    >>> mgr.list_views(schema="public")
    >>> mgr.find_column("email")
    >>> mgr.tail("users", limit=10)
    >>> mgr.table_activity_status("events", max_age_days=7)
    """
    return SchemaManager(get_engine(url))


# ---------------------------------------------------------------------------
# SchemaAnalyzer — DataFrame vs table analysis
# ---------------------------------------------------------------------------

def analyze(
    table: str,
    df: Optional[pd.DataFrame] = None,
    url: Optional[str] = None,
    schema: Optional[str] = None,
    run_fk_checks: bool = False,
) -> TableAnalysisReport:
    """
    Analyze a database table and optionally validate a DataFrame against it.

    Returns a TableAnalysisReport containing:
    - engine_info: dialect, driver, connection status
    - columns: ColumnInfo per column (type, nullable, length, precision)
    - constraints: PKs, FKs, unique constraints, indexes
    - dialect_checks: dialect-specific warnings (Oracle empty strings,
      MySQL zero-dates, MSSQL BIT, PostgreSQL ARRAY/JSON)
    - df_info: DataFrame dtypes and null counts (if df provided)
    - mapping: column alignment between df and table (if df provided)
    - validation: NOT NULL, UNIQUE, FK violations with suggestions (if df provided)

    Parameters
    ----------
    table : str
        Target table name.
    df : pd.DataFrame, optional
        DataFrame to validate against the table schema.
    url : str, optional
        Database URL.
    schema : str, optional
        Database schema/namespace.
    run_fk_checks : bool, default False
        If True, queries parent tables to validate FK integrity.

    Returns
    -------
    TableAnalysisReport

    Examples
    --------
    >>> import peek as pk
    >>> report = pk.analyze("users", url="postgresql://localhost/mydb")
    >>> print(report.dialect_checks.suggestions)

    >>> import pandas as pd
    >>> df = pd.read_csv("users.csv")
    >>> report = pk.analyze("users", df=df)
    >>> print(report.validation.issues)
    >>> print(report.mapping.suggestions)
    """
    engine = get_engine(url)
    return _analyze_table_fn(engine, schema=schema, table_name=table,
                             df=df, run_fk_checks=run_fk_checks)


# ---------------------------------------------------------------------------
# SchemaAligner — align and correct DataFrame types to match SQL schema
# ---------------------------------------------------------------------------

def align(
    df: pd.DataFrame,
    table: str,
    url: Optional[str] = None,
    schema: Optional[str] = None,
    on_error: str = 'coerce',
    failure_threshold: float = 0.1,
    validate_fk: bool = False,
    add_missing_cols: bool = False,
    col_map: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Align a DataFrame to a SQL table's schema with strict type enforcement.

    Coerces each column to the target SQL type, enforces VARCHAR length limits,
    validates NOT NULL constraints, detects outliers via IsolationForest,
    and optionally adds missing columns via ALTER TABLE.

    Parameters
    ----------
    df : pd.DataFrame
        Source DataFrame to align.
    table : str
        Target table name.
    url : str, optional
        Database URL.
    schema : str, optional
        Database schema/namespace.
    on_error : str, default 'coerce'
        'coerce' — nullify failing values and continue.
        'raise'  — raise ValueError on threshold breach.
    failure_threshold : float, default 0.1
        Max fraction of coercion failures before aborting (0.1 = 10%).
    validate_fk : bool, default False
        If True, validates FK integrity before returning.
    add_missing_cols : bool, default False
        If True, ALTER TABLE to add df columns missing from the table.
    col_map : dict, optional
        Explicit {DataFrame column: SQL column} alias mapping.

    Returns
    -------
    pd.DataFrame
        Aligned DataFrame with corrected types, ordered to match table columns.

    Examples
    --------
    >>> import peek as pk
    >>> import pandas as pd
    >>> df = pd.read_csv("users.csv")
    >>> aligned = pk.align(df, "users", url="postgresql://localhost/mydb")
    """
    engine = get_engine(url)
    aligner = SchemaAligner(
        conn=engine,
        on_error=on_error,
        failure_threshold=failure_threshold,
        validate_fk=validate_fk,
        add_missing_cols=add_missing_cols,
        col_map=col_map,
    )
    return aligner.align(df, table, schema=schema)


# ---------------------------------------------------------------------------
# df_align_to_sql utilities
# ---------------------------------------------------------------------------

def align_df(
    df: pd.DataFrame,
    table: str,
    url: Optional[str] = None,
    schema: Optional[str] = None,
    threshold: float = 10.0,
    fix_outliers: bool = False,
    auto_alter: bool = False,
    outlier_method: str = 'iqr',
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Align a DataFrame to a SQL table schema and return (aligned_df, report).

    Lighter alternative to align() — uses column-level coercion with a
    null-increase threshold rather than strict type enforcement.
    Returns a detailed per-column report for observability.

    Parameters
    ----------
    df : pd.DataFrame
        Source DataFrame.
    table : str
        Target table name.
    url : str, optional
        Database URL.
    schema : str, optional
        Database schema/namespace.
    threshold : float, default 10.0
        Max allowed null-percentage increase per column before flagging.
    fix_outliers : bool, default False
        If True, detect and replace outliers with NA before coercion.
    auto_alter : bool, default False
        If True, ALTER TABLE to add extra df columns not in the table.
    outlier_method : str, default 'iqr'
        Outlier detection method: 'iqr' or 'zscore'.

    Returns
    -------
    Tuple[pd.DataFrame, Dict]
        aligned_df — coerced DataFrame ordered to match table columns.
        report     — per-column stats: null counts, outliers removed, errors.

    Examples
    --------
    >>> import peek as pk
    >>> aligned, report = pk.align_df(df, "users", fix_outliers=True)
    >>> print(report['columns_failed'])
    >>> print(report['outliers_removed'])
    """
    engine = get_engine(url)
    return align_dataframe_to_schema(
        df=df, engine=engine, table=table, schema=schema,
        threshold=threshold, fix_outliers=fix_outliers,
        auto_alter=auto_alter, outlier_method=outlier_method,
    )


# Convenience aliases for quick access
show_tables = tables
table_info = describe_full
table_exists = has_table

__all__ = [
    # Engine
    'get_engine',
    # Introspection — stateless
    'tables',
    'show_tables',
    'describe',
    'describe_full',
    'table_info',
    'has_table',
    'table_exists',
    'get_pk',
    # Query execution
    'query',
    'query_clean',
    # Constraint validation
    'validate_upsert',
    # SchemaManager — rich read-only introspection
    'get_manager',
    # SchemaAnalyzer — DataFrame vs table analysis
    'analyze',
    # SchemaAligner — strict type enforcement
    'align',
    # df_align_to_sql — lightweight alignment with report
    'align_df',
    # df_align_to_sql utilities
    'normalize_sql_type',
    'detect_outliers',
    'correct_outliers',
    'generate_schema',
    # Re-exported dataclasses from schema_analyzer
    'TableAnalysisReport',
    'ColumnInfo',
    'ConstraintInfo',
    'MappingInfo',
    'ValidationSummary',
    'DialectChecks',
    'EngineInfo',
]
