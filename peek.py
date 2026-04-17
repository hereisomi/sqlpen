"""
peek.py — Quick database introspection and query execution.

A lightweight, standalone module for:
- Listing tables
- Describing table schemas  
- Executing arbitrary SQL queries

Uses SchemaInspector from misc/ for robust introspection.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Import existing utilities
from misc.schema_inspector import SchemaInspector
from config import get_engine_from_env

# Singleton inspector instance
_inspector = SchemaInspector()


def get_engine(url: Optional[str] = None) -> Engine:
    """
    Resolve a SQLAlchemy Engine.
    
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
    return get_engine_from_env(url)


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


# Convenience aliases for quick access
show_tables = tables
table_info = describe_full
table_exists = has_table

__all__ = [
    # Engine
    'get_engine',
    # Introspection
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
    # Validation
    'validate_upsert',
]
