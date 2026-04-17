"""
misc — Internal schema utilities for SqlPen.

Exposes the public API of all misc modules so they can be imported
cleanly from a single location when needed directly.

Primary access point is peek.py which wraps these behind a stateless
URL-based API. Direct use of these classes requires managing the engine
lifecycle manually.
"""
from .schema_inspector import SchemaInspector
from .schema_analyzer import (
    SchemaAnalyzer,
    analyze_table,
    TableAnalysisReport,
    ColumnInfo,
    ConstraintInfo,
    MappingInfo,
    ValidationSummary,
    DialectChecks,
    EngineInfo,
    DataFrameInfo,
)
from .schema_manager import SchemaManager
from .schema_corrector import SchemaAligner
from .df_align_to_sql import (
    align_dataframe_to_schema,
    normalize_sql_type,
    detect_outliers,
    correct_outliers,
    generate_schema,
    align_column,
    infer_sql_type,
    coerce_series,
)
from .engine_manager import EngineManager

__all__ = [
    # Stateless inspector (used internally by peek.py)
    "SchemaInspector",
    # Schema analysis — read-only DataFrame vs table validation
    "SchemaAnalyzer",
    "analyze_table",
    "TableAnalysisReport",
    "ColumnInfo",
    "ConstraintInfo",
    "MappingInfo",
    "ValidationSummary",
    "DialectChecks",
    "EngineInfo",
    "DataFrameInfo",
    # Rich stateful introspection
    "SchemaManager",
    # Strict type alignment with IsolationForest outlier detection
    "SchemaAligner",
    # Lightweight alignment with per-column report
    "align_dataframe_to_schema",
    "align_column",
    "normalize_sql_type",
    "detect_outliers",
    "correct_outliers",
    "generate_schema",
    "infer_sql_type",
    "coerce_series",
    # Pooled engine registry with audit logging
    "EngineManager",
]
