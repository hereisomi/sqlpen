"""
Unified facade for ETL workflow execution specifically for Python dictionaries.

Provides `dict_tosql` which orchestrates cleaning, casting, profiling, and 
targeted CRUD execution automatically by seamlessly routing payloads 
into the `df_tosql` core engine.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from sqlalchemy.engine import Engine

from crud import CrudResult
from .df_tosql import df_tosql

logger = logging.getLogger(__name__)


def dict_tosql(
    data: Union[Dict[str, Any], List[Dict[str, Any]]],
    table: str,
    engine: Optional[Engine] = None,
    if_exist: str = 'insert',
    schema: Optional[str] = None,
    chunk: int = 1000,
    constraint_cols: Union[List[str], str, None] = '',
    where: Optional[List[Any]] = None,
    expression: Optional[str] = None,
    add_new_column: bool = True,
    clean: bool = True,
    cast: bool = True,
    auto_profiling: bool = False,
    outlier: float = 0.5,
    schema_name: str = 'abc.json'
) -> CrudResult:
    """
    Pipeline facade to write dictionary objects to a SQL database.
    Integrates cleaning, casting, outlier processing, and schema metadata dump.

    Parameters
    ----------
    data : dict or List[dict]
        Source data. Accepts a single dictionary or a JSON-like list of dictionaries.
    table : str
        Target table name.
    engine : Engine, optional
        SQLAlchemy engine. If None, auto-resolved from DATABASE_URL env var.
        
    (Inherits all other args and behaviors from df_tosql).
    """
    if not data:
        raise ValueError("Source dict/list is empty.")

    # Normalize pure dictionary into a list container
    if isinstance(data, dict):
        data = [data]

    # Convert to DataFrame to inherit robust formatting algorithms
    logger.info("Pipeline: Injecting dictionary payload (%d records) into df_tosql engine", len(data))
    df = pd.DataFrame(data)

    return df_tosql(
        df=df,
        table=table,
        engine=engine,
        if_exist=if_exist,
        schema=schema,
        chunk=chunk,
        constraint_cols=constraint_cols,
        where=where,
        expression=expression,
        add_new_column=add_new_column,
        clean=clean,
        cast=cast,
        auto_profiling=auto_profiling,
        outlier=outlier,
        schema_name=schema_name
    )
