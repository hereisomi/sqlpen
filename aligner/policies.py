"""
Policy configuration for DataFrame-to-SQL alignment.

Defines safe defaults and configurable behavior for coercion,
outlier handling, and DDL operations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExtraDfColumnsAction(Enum):
    """Action for DataFrame columns not present in target table."""
    DROP = "drop"
    KEEP = "keep"
    ERROR = "error"


class OutlierAction(Enum):
    """Action to take on detected outliers."""
    DROP = "drop"
    NULLIFY = "nullify"
    CLIP = "clip"


class StringOverflowAction(Enum):
    """Action when string exceeds target length."""
    TRUNCATE = "truncate"
    ERROR = "error"


class NumericOverflowAction(Enum):
    """Action when numeric value exceeds target precision/scale."""
    ROUND = "round"
    ERROR = "error"


class DatetimeTzPolicy(Enum):
    """How to handle timezone mismatches."""
    ASSUME_UTC = "assume_utc"
    ASSUME_LOCAL = "assume_local"
    ERROR = "error"


@dataclass
class OutlierPolicy:
    """Policy for outlier detection and correction."""
    enabled: bool = False
    max_pct_total_rows: float = 0.05  # Hard cap: 5%
    action: OutlierAction = OutlierAction.DROP
    method: str = "iqr"  # "iqr", "mad", "zscore"
    combine_rule: str = "any"  # "any", "all"
    iqr_factor: float = 1.5
    mad_factor: float = 3.0
    zscore_threshold: float = 3.0


@dataclass
class ColumnPolicy:
    """Policy for column handling."""
    extra_df_columns_action: ExtraDfColumnsAction = ExtraDfColumnsAction.DROP
    string_overflow_action: StringOverflowAction = StringOverflowAction.TRUNCATE
    numeric_overflow_action: NumericOverflowAction = NumericOverflowAction.ROUND
    datetime_tz_policy: DatetimeTzPolicy = DatetimeTzPolicy.ASSUME_UTC
    allow_null_insert_for_not_null: bool = False
    bool_to_int: bool = True  # Convert True/False to 1/0 for integer columns


@dataclass
class DdlPolicy:
    """Policy for DDL operations."""
    enabled: bool = False
    dry_run: bool = True
    allow_add_columns: bool = True
    allow_widen_columns: bool = True
    allow_alter_type: bool = False  # Generally unsafe
    allow_add_indexes: bool = True
    lock_timeout_seconds: int = 30
    statement_timeout_seconds: int = 300
    batch_ddl: bool = True
    max_batch_size: int = 10


@dataclass
class ValidationPolicy:
    """Policy for validation rules."""
    check_nullability: bool = True
    check_constraints: bool = True
    check_referential_integrity: bool = False
    max_sample_rows: int = 1000
    confidence_threshold: float = 0.8


@dataclass
class AlignmentPolicies:
    """Complete policy configuration for alignment operations."""
    columns: ColumnPolicy = field(default_factory=ColumnPolicy)
    outliers: OutlierPolicy = field(default_factory=OutlierPolicy)
    ddl: DdlPolicy = field(default_factory=DdlPolicy)
    validation: ValidationPolicy = field(default_factory=ValidationPolicy)
    
    # Global settings
    strict_mode: bool = False
    verbose_reporting: bool = True
    max_memory_mb: Optional[int] = None  # For large DataFrames


# Default policy instance
DEFAULT_POLICIES = AlignmentPolicies()


def validate_policies(policies: AlignmentPolicies) -> None:
    """Validate policy configuration for safety."""
    if policies.outliers.max_pct_total_rows > 0.05:
        raise ValueError("outliers.max_pct_total_rows cannot exceed 5%")
    
    if policies.ddl.statement_timeout_seconds < 10:
        raise ValueError("statement_timeout_seconds must be at least 10")
    
    if policies.validation.max_sample_rows < 0:
        raise ValueError("max_sample_rows must be non-negative")
    
    if policies.validation.confidence_threshold < 0 or policies.validation.confidence_threshold > 1:
        raise ValueError("confidence_threshold must be between 0 and 1")
