"""
DataFrame-to-SQL Alignment Package
"""

from typing import Optional

from .policies import (
    AlignmentPolicies, DEFAULT_POLICIES, validate_policies,
    ExtraDfColumnsAction, OutlierAction, StringOverflowAction,
    NumericOverflowAction, DatetimeTzPolicy,
)
from .models import (
    ColumnSpec, TableSpec, ConstraintSpec, IndexSpec, EngineInfo,
    AnalysisReport, AlignmentPlan, DdlPlan, DdlAction,
    CoercionReport, ExecutionReport, Issue, IssueCode, Severity,
    ColumnMapping, OutlierResult,
)
from .type_system import TypeFamily, infer_type_family, extract_limits, get_canonical_sql_type, is_type_compatible
from .mapping import normalize_identifier, build_column_mapping, validate_mapping
from .analyze import analyze
from .outliers import detect_outliers, apply_outlier_action, validate_outlier_parameters
from .coercion import coerce_dataframe
from .ddl_plan import generate_ddl_plan, validate_ddl_plan, batch_ddl_actions
from .engine import get_engine_info, get_capabilities, is_safe_ddl_operation
from .reflect import reflect_table_spec, reflect_all_tables
from .execute import apply_ddl_plan
from .router import align

__version__ = "1.0.0"

__all__ = [
    # pipeline essentials — what most callers need
    "reflect_table_spec",
    "analyze",
    "coerce_dataframe",
    "apply_ddl_plan",
    "align",
    # config
    "AlignmentPolicies",
    "DEFAULT_POLICIES",
    "validate_policies",
    # enums
    "ExtraDfColumnsAction", "OutlierAction", "StringOverflowAction",
    "NumericOverflowAction", "DatetimeTzPolicy",
    "IssueCode", "Severity", "TypeFamily",
    # models
    "ColumnSpec", "TableSpec", "ConstraintSpec", "IndexSpec", "EngineInfo",
    "AnalysisReport", "AlignmentPlan", "DdlPlan", "DdlAction",
    "CoercionReport", "ExecutionReport", "Issue", "ColumnMapping", "OutlierResult",
    # advanced / optional
    "infer_type_family", "extract_limits", "get_canonical_sql_type", "is_type_compatible",
    "normalize_identifier", "build_column_mapping", "validate_mapping",
    "detect_outliers", "apply_outlier_action", "validate_outlier_parameters",
    "generate_ddl_plan", "validate_ddl_plan", "batch_ddl_actions",
    "get_engine_info", "get_capabilities", "is_safe_ddl_operation",
    "reflect_all_tables",
]
