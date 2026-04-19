"""
Core data models for DataFrame-to-SQL alignment.

Defines the canonical data structures used across all aligner components.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Union
from datetime import datetime


class Severity(Enum):
    """Issue severity levels."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCode(Enum):
    """Standardized issue codes for consistent handling."""
    # Column issues
    EXTRA_DF_COLUMN = "EXTRA_DF_COLUMN"
    MISSING_DB_COLUMN = "MISSING_DB_COLUMN"
    COLUMN_TYPE_MISMATCH = "COLUMN_TYPE_MISMATCH"
    COLUMN_NULLABILITY_VIOLATION = "COLUMN_NULLABILITY_VIOLATION"
    COLUMN_LENGTH_OVERFLOW = "COLUMN_LENGTH_OVERFLOW"
    COLUMN_PRECISION_OVERFLOW = "COLUMN_PRECISION_OVERFLOW"
    
    # Outlier issues
    OUTLIER_RATE_EXCEEDS_CAP = "OUTLIER_RATE_EXCEEDS_CAP"
    OUTLIERS_DETECTED = "OUTLIERS_DETECTED"
    
    # Constraint issues
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"
    REFERENTIAL_INTEGRITY_VIOLATION = "REFERENTIAL_INTEGRITY_VIOLATION"
    
    # DDL issues
    DDL_EXECUTION_FAILED = "DDL_EXECUTION_FAILED"
    UNSAFE_DDL_OPERATION = "UNSAFE_DDL_OPERATION"
    
    # General issues
    MAPPING_CONFIDENCE_LOW = "MAPPING_CONFIDENCE_LOW"
    DATETIME_TZ_MISMATCH = "DATETIME_TZ_MISMATCH"
    CONVERSION_ERROR = "CONVERSION_ERROR"


@dataclass
class Issue:
    """Represents an issue found during analysis or coercion."""
    code: IssueCode
    severity: Severity
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
    column_name: Optional[str] = None
    row_indices: Optional[List[int]] = None
    sample_values: Optional[List[Any]] = None


@dataclass
class ColumnSpec:
    """Specification for a database column."""
    name: str
    type_family: str  # From TypeFamily enum
    sql_type: str  # Full SQL type string
    nullable: bool = True
    default_value: Optional[Any] = None
    max_length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    timezone: Optional[bool] = None
    auto_increment: bool = False
    comment: Optional[str] = None
    
    # Oracle-specific
    char_semantics: Optional[str] = None  # "CHAR" or "BYTE"
    identity: Optional[Dict[str, Any]] = None


@dataclass
class ConstraintSpec:
    """Specification for a table constraint."""
    name: str
    type: str  # "PRIMARY_KEY", "FOREIGN_KEY", "UNIQUE", "CHECK"
    columns: List[str]
    definition: Optional[str] = None
    references_table: Optional[str] = None
    references_columns: Optional[List[str]] = None


@dataclass
class IndexSpec:
    """Specification for a table index."""
    name: str
    columns: List[str]
    unique: bool = False
    type: str = "BTREE"  # BTREE, HASH, GIN, etc.
    definition: Optional[str] = None


@dataclass
class TableSpec:
    """Specification for a database table."""
    schema: str
    name: str
    columns: Dict[str, ColumnSpec]
    constraints: Dict[str, ConstraintSpec] = field(default_factory=dict)
    indexes: Dict[str, IndexSpec] = field(default_factory=dict)
    comment: Optional[str] = None
    
    def get_column(self, name: str) -> Optional[ColumnSpec]:
        """Get column specification by name."""
        return self.columns.get(name)
    
    def has_column(self, name: str) -> bool:
        """Check if table has a column."""
        return name in self.columns


@dataclass
class EngineInfo:
    """Information about the database engine."""
    dialect: str
    version: str
    server_version: Optional[str] = None
    capabilities: Dict[str, bool] = field(default_factory=dict)


@dataclass
class ColumnMapping:
    """Mapping between DataFrame column and table column."""
    df_column: str
    table_column: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    transformation: Optional[str] = None


@dataclass
class OutlierResult:
    """Result of outlier detection."""
    total_rows: int
    outlier_rows: int
    outlier_indices: List[int]
    outlier_columns: List[str]
    method: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def outlier_percentage(self) -> float:
        """Percentage of rows that are outliers."""
        if self.total_rows == 0:
            return 0.0
        return (self.outlier_rows / self.total_rows) * 100


@dataclass
class AnalysisReport:
    """Report from analysis phase."""
    table_spec: TableSpec
    column_mappings: List[ColumnMapping]
    issues: List[Issue] = field(default_factory=list)
    extra_df_columns: List[str] = field(default_factory=list)
    missing_db_columns: List[str] = field(default_factory=list)
    outlier_result: Optional[OutlierResult] = None
    
    # Statistics
    total_columns: int = 0
    mapped_columns: int = 0
    confidence_score: float = 0.0
    
    def get_issues_by_severity(self, severity: Severity) -> List[Issue]:
        """Get issues filtered by severity."""
        return [issue for issue in self.issues if issue.severity == severity]
    
    def has_errors(self) -> bool:
        """Check if there are any error-level issues."""
        return any(issue.severity == Severity.ERROR for issue in self.issues)


@dataclass
class AlignmentPlan:
    """Plan for aligning DataFrame to table structure."""
    column_actions: Dict[str, str] = field(default_factory=dict)
    transformations: Dict[str, str] = field(default_factory=dict)
    drop_columns: List[str] = field(default_factory=list)
    outlier_action: Optional[str] = None
    
    def should_drop_column(self, column: str) -> bool:
        """Check if column should be dropped."""
        return column in self.drop_columns


@dataclass
class DdlAction:
    """Single DDL operation to be executed."""
    action_type: str  # "ADD_COLUMN", "ALTER_COLUMN", "DROP_COLUMN", "ADD_INDEX"
    table_name: str
    column_name: Optional[str] = None
    sql_type: Optional[str] = None
    definition: Optional[str] = None
    safe: bool = True


@dataclass
class DdlPlan:
    """Plan for DDL operations."""
    actions: List[DdlAction] = field(default_factory=list)
    estimated_execution_time_seconds: Optional[float] = None
    
    @property
    def has_unsafe_operations(self) -> bool:
        """Check if plan contains unsafe operations."""
        return any(not action.safe for action in self.actions)
    
    def get_safe_actions(self) -> List[DdlAction]:
        """Get only safe DDL actions."""
        return [action for action in self.actions if action.safe]


@dataclass
class CoercionReport:
    """Report from DataFrame coercion phase."""
    original_shape: tuple = (0, 0)
    final_shape: tuple = (0, 0)
    dropped_rows: int = 0
    dropped_columns: List[str] = field(default_factory=list)
    transformed_columns: List[str] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)
    
    # Per-column statistics
    column_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    @property
    def rows_processed(self) -> int:
        """Number of rows processed (original - dropped)."""
        return self.original_shape[0] - self.dropped_rows
    
    @property
    def columns_processed(self) -> int:
        """Number of columns processed (original - dropped)."""
        return self.original_shape[1] - len(self.dropped_columns)


@dataclass
class ExecutionReport:
    """Report from DDL execution phase."""
    ddl_plan: DdlPlan
    executed_actions: List[DdlAction] = field(default_factory=list)
    failed_actions: List[DdlAction] = field(default_factory=list)
    execution_time_seconds: float = 0.0
    issues: List[Issue] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        """Percentage of actions executed successfully."""
        if not self.ddl_plan.actions:
            return 1.0
        return len(self.executed_actions) / len(self.ddl_plan.actions)
