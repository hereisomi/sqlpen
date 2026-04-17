"""
SqlPen: Hardened ETL and Schema-Aware CRUD Engine.
Unified API exported for SqlPen.
Imports and surfaces key objects from across the pipeline, crud, config, and utility modules
so they can be cleanly imported from a single location.
"""
from __future__ import annotations

# 1. Core Pipelines
from .pipeline.df_tosql import df_tosql
from .pipeline.dict_tosql import dict_tosql
from .pipeline.csv_harness import run_csv_pipeline, PipelineRunner, PipelineReport
from .pipeline.oracle_monitor import run_oracle_audit
from .pipeline.csvdog import csvdog

# 2. Database CRUD & Schema Engines
from .crud import auto_insert, auto_upsert, auto_update, CrudConfig, CrudResult
from .crud.schema import ColumnInfo
from .crud.fingerprint import build_fingerprint, diff_fingerprints, SchemaFingerprint, SchemaDiff

# 3. Setup & Configuration 
from .config import load_pipeline_config, build_crud_config, ensure_table, get_engine_from_env

# 4. Utilities: Profiler
from .utils.profiler import profile_dataframe, get_pk, sample_dispatcher

# 5. Utilities: Data Cleanse & Cast
from .utils.trycast import auto_cast, replace_outliers_with_zero_safe

# 6. Utilities: DDL & Sanitation
from .utils.ddl import sanitize_dataframe_columns, df_to_ddl_and_schema

# 7. Utilities: Telemetry
from .utils.logger import log_call, log_dataframe, log_json

# 8. Quick Introspection (peek)
from .peek import (
    tables, show_tables, describe, describe_full, table_info,
    has_table, table_exists, query, query_clean,
    validate_upsert, get_engine,
)

__all__ = [
    # Pipeline
    "df_tosql",
    "dict_tosql",
    "run_csv_pipeline",
    "run_oracle_audit",
    "PipelineRunner",
    "PipelineReport",
    "csvdog",
    
    # CRUD
    "auto_insert",
    "auto_upsert",
    "auto_update",
    "CrudConfig",
    "CrudResult",
    "ColumnInfo",
    
    # Schema Fingerprinting
    "build_fingerprint",
    "diff_fingerprints",
    "SchemaFingerprint",
    "SchemaDiff",
    
    # Configuration / Table Setup
    "load_pipeline_config",
    "build_crud_config",
    "ensure_table",
    "get_engine_from_env",

    # Profilers
    "profile_dataframe",
    "get_pk",
    "sample_dispatcher",

    # ETL Transformers
    "auto_cast",
    "replace_outliers_with_zero_safe",

    # SQL DDL Parsers
    "sanitize_dataframe_columns",
    "df_to_ddl_and_schema",

    # Telemetry Loggers
    "log_call",
    "log_dataframe",
    "log_json",

    # Quick Introspection (peek)
    "tables",
    "show_tables",
    "describe",
    "describe_full",
    "table_info",
    "has_table",
    "table_exists",
    "query",
    "query_clean",
    "validate_upsert",
    "get_engine",
]

