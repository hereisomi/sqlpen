"""
DDL plan generation for schema updates.

Generates safe DDL operations to align database schema with DataFrame requirements,
with dialect-specific considerations and safety guards.
"""

from typing import List, Dict, Any, Optional, Tuple
from .models import (
    TableSpec, ColumnSpec, DdlPlan, DdlAction, Issue, IssueCode, Severity,
    AnalysisReport, AlignmentPlan
)
from .policies import AlignmentPolicies, DEFAULT_POLICIES
from .type_system import TypeFamily, get_canonical_sql_type, is_type_compatible


def analyze_column_additions(
    df_analysis: Dict[str, Any],
    table_spec: TableSpec,
    policies: AlignmentPolicies
) -> List[DdlAction]:
    """
    Analyze DataFrame columns that need to be added to the table.
    
    Args:
        df_analysis: DataFrame analysis results
        table_spec: Current table specification
        policies: Alignment policies
        
    Returns:
        List of DDL actions for adding columns
    """
    actions = []
    
    if not policies.ddl.allow_add_columns:
        return actions
    
    df_columns = df_analysis.get('columns', [])
    table_columns = list(table_spec.columns.keys())
    
    # Find columns in DataFrame that don't exist in table
    missing_columns = [col for col in df_columns if col not in table_columns]
    
    for col in missing_columns:
        df_dtype = df_analysis['dtypes'][col]
        
        # Infer appropriate SQL type from DataFrame dtype
        sql_type = infer_sql_type_from_dtype(df_dtype, "oracle")  # Default to Oracle
        
        action = DdlAction(
            action_type="ADD_COLUMN",
            table_name=f"{table_spec.schema}.{table_spec.name}",
            column_name=col,
            sql_type=sql_type,
            safe=True  # Adding columns is generally safe
        )
        actions.append(action)
    
    return actions


def analyze_column_widening(
    df_analysis: Dict[str, Any],
    table_spec: TableSpec,
    policies: AlignmentPolicies
) -> List[DdlAction]:
    """
    Analyze table columns that need to be widened to accommodate DataFrame data.
    
    Args:
        df_analysis: DataFrame analysis results
        table_spec: Current table specification
        policies: Alignment policies
        
    Returns:
        List of DDL actions for widening columns
    """
    actions = []
    
    if not policies.ddl.allow_widen_columns:
        return actions
    
    string_lengths = df_analysis.get('string_lengths', {})
    
    for col_name, col_spec in table_spec.columns.items():
        if col_name not in df_analysis['columns']:
            continue
        
        # Check string length widening
        if col_spec.type_family == TypeFamily.STRING and col_spec.max_length:
            df_length_info = string_lengths.get(col_name, {})
            max_df_length = df_length_info.get('max_length', 0)
            
            if max_df_length > col_spec.max_length:
                # Calculate new length (add 25% buffer)
                new_length = int(max_df_length * 1.25)
                new_sql_type = get_canonical_sql_type(
                    TypeFamily.STRING, 
                    "oracle", 
                    max_length=new_length,
                    char_semantics=col_spec.char_semantics
                )
                
                action = DdlAction(
                    action_type="ALTER_COLUMN",
                    table_name=f"{table_spec.schema}.{table_spec.name}",
                    column_name=col_name,
                    sql_type=new_sql_type,
                    safe=True  # Widening is generally safe
                )
                actions.append(action)
        
        # Check numeric precision widening
        elif col_spec.type_family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            numeric_stats = df_analysis.get('numeric_stats', {}).get(col_name, {})
            if numeric_stats:
                max_value = numeric_stats.get('max', 0)
                min_value = numeric_stats.get('min', 0)
                
                # Determine if current precision is insufficient
                if col_spec.precision:
                    max_supported = 10 ** col_spec.precision - 1
                    if abs(max_value) > max_supported or abs(min_value) > max_supported:
                        # Calculate new precision
                        required_precision = max(
                            len(str(int(abs(max_value)))),
                            len(str(int(abs(min_value))))
                        ) + 2  # Add buffer
                        
                        new_sql_type = get_canonical_sql_type(
                            col_spec.type_family,
                            "oracle",
                            precision=required_precision,
                            scale=col_spec.scale
                        )
                        
                        action = DdlAction(
                            action_type="ALTER_COLUMN",
                            table_name=f"{table_spec.schema}.{table_spec.name}",
                            column_name=col_name,
                            sql_type=new_sql_type,
                            safe=True  # Precision widening is generally safe
                        )
                        actions.append(action)
    
    return actions


def analyze_type_changes(
    df_analysis: Dict[str, Any],
    table_spec: TableSpec,
    policies: AlignmentPolicies
) -> List[DdlAction]:
    """
    Analyze table columns that need type changes to accommodate DataFrame data.
    
    Args:
        df_analysis: DataFrame analysis results
        table_spec: Current table specification
        policies: Alignment policies
        
    Returns:
        List of DDL actions for type changes
    """
    actions = []
    
    if not policies.ddl.allow_alter_type:
        return actions
    
    for col_name, col_spec in table_spec.columns.items():
        if col_name not in df_analysis['columns']:
            continue
        
        df_dtype = df_analysis['dtypes'][col_name]
        df_type_family = infer_type_family_from_dtype(df_dtype)
        
        # Check if type change is needed and safe
        if not is_type_compatible(df_type_family, col_spec.type_family, "oracle"):
            # This would be a type change, which is generally unsafe
            # Only include if explicitly allowed
            if policies.ddl.allow_alter_type:
                new_sql_type = get_canonical_sql_type(df_type_family, "oracle")
                
                action = DdlAction(
                    action_type="ALTER_COLUMN",
                    table_name=f"{table_spec.schema}.{table_spec.name}",
                    column_name=col_name,
                    sql_type=new_sql_type,
                    safe=False  # Type changes are unsafe
                )
                actions.append(action)
    
    return actions


def analyze_index_additions(
    table_spec: TableSpec,
    policies: AlignmentPolicies
) -> List[DdlAction]:
    """
    Analyze potential index additions for performance.
    
    Args:
        table_spec: Current table specification
        policies: Alignment policies
        
    Returns:
        List of DDL actions for adding indexes
    """
    actions = []
    
    if not policies.ddl.allow_add_indexes:
        return actions
    
    # Simple heuristic: suggest indexes on foreign key columns
    for constraint_name, constraint in table_spec.constraints.items():
        if constraint.type == "FOREIGN_KEY":
            for col in constraint.columns:
                # Check if index already exists
                index_exists = any(
                    col in index.columns 
                    for index in table_spec.indexes.values()
                )
                
                if not index_exists:
                    index_name = f"idx_{table_spec.name}_{col}"
                    
                    action = DdlAction(
                        action_type="ADD_INDEX",
                        table_name=f"{table_spec.schema}.{table_spec.name}",
                        definition=f"CREATE INDEX {index_name} ON {table_spec.name} ({col})",
                        safe=True  # Adding indexes is safe
                    )
                    actions.append(action)
    
    return actions


def infer_sql_type_from_dtype(dtype_str: str, dialect: str) -> str:
    """
    Infer appropriate SQL type from pandas dtype string.
    
    Args:
        dtype_str: Pandas dtype string
        dialect: Database dialect
        
    Returns:
        SQL type string
    """
    dtype_lower = dtype_str.lower()
    
    if 'int' in dtype_lower:
        return get_canonical_sql_type(TypeFamily.INTEGER, dialect)
    elif 'float' in dtype_lower:
        return get_canonical_sql_type(TypeFamily.FLOAT, dialect)
    elif 'decimal' in dtype_lower:
        return get_canonical_sql_type(TypeFamily.DECIMAL, dialect)
    elif 'bool' in dtype_lower:
        return get_canonical_sql_type(TypeFamily.BOOLEAN, dialect)
    elif 'datetime' in dtype_lower or 'timestamp' in dtype_lower:
        return get_canonical_sql_type(TypeFamily.DATETIME, dialect)
    elif 'date' in dtype_lower and 'datetime' not in dtype_lower:
        return get_canonical_sql_type(TypeFamily.DATE, dialect)
    elif 'time' in dtype_lower and 'datetime' not in dtype_lower:
        return get_canonical_sql_type(TypeFamily.TIME, dialect)
    elif 'object' in dtype_lower:
        # Default to VARCHAR(255) for object dtype
        return get_canonical_sql_type(TypeFamily.STRING, dialect, max_length=255)
    else:
        return get_canonical_sql_type(TypeFamily.STRING, dialect, max_length=255)


def infer_type_family_from_dtype(dtype_str: str) -> TypeFamily:
    """
    Infer TypeFamily from pandas dtype string.
    
    Args:
        dtype_str: Pandas dtype string
        
    Returns:
        TypeFamily
    """
    dtype_lower = dtype_str.lower()
    
    if 'int' in dtype_lower:
        return TypeFamily.INTEGER
    elif 'float' in dtype_lower:
        return TypeFamily.FLOAT
    elif 'decimal' in dtype_lower:
        return TypeFamily.DECIMAL
    elif 'bool' in dtype_lower:
        return TypeFamily.BOOLEAN
    elif 'datetime' in dtype_lower or 'timestamp' in dtype_lower:
        return TypeFamily.DATETIME
    elif 'date' in dtype_lower and 'datetime' not in dtype_lower:
        return TypeFamily.DATE
    elif 'time' in dtype_lower and 'datetime' not in dtype_lower:
        return TypeFamily.TIME
    elif 'object' in dtype_lower:
        return TypeFamily.STRING
    else:
        return TypeFamily.UNKNOWN


def generate_ddl_plan(
    analysis_report: AnalysisReport,
    alignment_plan: AlignmentPlan,
    df_analysis: Optional[Dict[str, Any]] = None,
    policies: Optional[AlignmentPolicies] = None
) -> DdlPlan:
    """
    Generate comprehensive DDL plan based on analysis results.
    
    Args:
        analysis_report: Results from DataFrame analysis
        alignment_plan: Plan for column alignment
        df_analysis: Optional DataFrame analysis details
        policies: Alignment policies (uses defaults if None)
        
    Returns:
        Comprehensive DDL plan
    """
    if policies is None:
        policies = DEFAULT_POLICIES
    
    if not policies.ddl.enabled:
        return DdlPlan()
    
    all_actions = []
    
    # If df_analysis is not provided, create a minimal one
    if df_analysis is None:
        df_analysis = {
            'columns': [mapping.df_column for mapping in analysis_report.column_mappings],
            'dtypes': {mapping.df_column: 'object' for mapping in analysis_report.column_mappings},
            'string_lengths': {},
            'numeric_stats': {}
        }
    
    # Analyze different types of DDL operations
    addition_actions = analyze_column_additions(df_analysis, analysis_report.table_spec, policies)
    widening_actions = analyze_column_widening(df_analysis, analysis_report.table_spec, policies)
    type_change_actions = analyze_type_changes(df_analysis, analysis_report.table_spec, policies)
    index_actions = analyze_index_additions(analysis_report.table_spec, policies)
    
    # Combine all actions
    all_actions.extend(addition_actions)
    all_actions.extend(widening_actions)
    all_actions.extend(type_change_actions)
    all_actions.extend(index_actions)
    
    # Estimate execution time (very rough heuristic)
    safe_actions = [action for action in all_actions if action.safe]
    unsafe_actions = [action for action in all_actions if not action.safe]
    
    estimated_time = len(safe_actions) * 5 + len(unsafe_actions) * 30  # seconds
    
    return DdlPlan(
        actions=all_actions,
        estimated_execution_time_seconds=estimated_time
    )


def validate_ddl_plan(ddl_plan: DdlPlan, policies: AlignmentPolicies) -> List[Issue]:
    """
    Validate DDL plan for safety and policy compliance.
    
    Args:
        ddl_plan: DDL plan to validate
        policies: Alignment policies
        
    Returns:
        List of validation issues
    """
    issues = []
    
    # Check for unsafe operations
    unsafe_actions = [action for action in ddl_plan.actions if not action.safe]
    if unsafe_actions and not policies.strict_mode:
        issues.append(Issue(
            code=IssueCode.UNSAFE_DDL_OPERATION,
            severity=Severity.WARNING,
            message=f"DDL plan contains {len(unsafe_actions)} unsafe operations",
            context={'unsafe_actions': [action.action_type for action in unsafe_actions]}
        ))
    elif unsafe_actions and policies.strict_mode:
        issues.append(Issue(
            code=IssueCode.UNSAFE_DDL_OPERATION,
            severity=Severity.ERROR,
            message=f"DDL plan contains unsafe operations in strict mode",
            context={'unsafe_actions': [action.action_type for action in unsafe_actions]}
        ))
    
    # Check for disallowed operation types
    if not policies.ddl.allow_add_columns:
        add_actions = [action for action in ddl_plan.actions if action.action_type == "ADD_COLUMN"]
        if add_actions:
            issues.append(Issue(
                code=IssueCode.UNSAFE_DDL_OPERATION,
                severity=Severity.ERROR,
                message=f"ADD_COLUMN operations not allowed but {len(add_actions)} found"
            ))
    
    if not policies.ddl.allow_widen_columns:
        alter_actions = [action for action in ddl_plan.actions if action.action_type == "ALTER_COLUMN"]
        if alter_actions:
            issues.append(Issue(
                code=IssueCode.UNSAFE_DDL_OPERATION,
                severity=Severity.ERROR,
                message=f"ALTER_COLUMN operations not allowed but {len(alter_actions)} found"
            ))
    
    if not policies.ddl.allow_add_indexes:
        index_actions = [action for action in ddl_plan.actions if action.action_type == "ADD_INDEX"]
        if index_actions:
            issues.append(Issue(
                code=IssueCode.UNSAFE_DDL_OPERATION,
                severity=Severity.ERROR,
                message=f"ADD_INDEX operations not allowed but {len(index_actions)} found"
            ))
    
    # Check batch size limits
    if policies.ddl.batch_ddl and len(ddl_plan.actions) > policies.ddl.max_batch_size:
        issues.append(Issue(
            code=IssueCode.UNSAFE_DDL_OPERATION,
            severity=Severity.WARNING,
            message=f"DDL plan has {len(ddl_plan.actions)} actions, exceeds batch limit of {policies.ddl.max_batch_size}"
        ))
    
    return issues


def batch_ddl_actions(ddl_plan: DdlPlan, batch_size: int) -> List[DdlPlan]:
    """
    Split DDL plan into batches for safer execution.
    
    Args:
        ddl_plan: DDL plan to batch
        batch_size: Maximum actions per batch
        
    Returns:
        List of DDL plans, one per batch
    """
    if len(ddl_plan.actions) <= batch_size:
        return [ddl_plan]
    
    batches = []
    safe_actions = [action for action in ddl_plan.actions if action.safe]
    unsafe_actions = [action for action in ddl_plan.actions if not action.safe]
    
    # Process safe actions in batches
    for i in range(0, len(safe_actions), batch_size):
        batch_actions = safe_actions[i:i + batch_size]
        batch_plan = DdlPlan(
            actions=batch_actions,
            estimated_execution_time_seconds=len(batch_actions) * 5
        )
        batches.append(batch_plan)
    
    # Process unsafe actions individually (or in very small batches)
    for action in unsafe_actions:
        batch_plan = DdlPlan(
            actions=[action],
            estimated_execution_time_seconds=30
        )
        batches.append(batch_plan)
    
    return batches


def get_ddl_summary(ddl_plan: DdlPlan) -> Dict[str, Any]:
    """
    Get a comprehensive summary of the DDL plan.
    
    Args:
        ddl_plan: DDL plan to summarize
        
    Returns:
        Summary dictionary
    """
    action_counts = {}
    safe_counts = {}
    unsafe_counts = {}
    
    for action in ddl_plan.actions:
        action_type = action.action_type
        action_counts[action_type] = action_counts.get(action_type, 0) + 1
        
        if action.safe:
            safe_counts[action_type] = safe_counts.get(action_type, 0) + 1
        else:
            unsafe_counts[action_type] = unsafe_counts.get(action_type, 0) + 1
    
    summary = {
        'total_actions': len(ddl_plan.actions),
        'safe_actions': len(ddl_plan.get_safe_actions()),
        'unsafe_actions': len(ddl_plan.actions) - len(ddl_plan.get_safe_actions()),
        'estimated_execution_time_seconds': ddl_plan.estimated_execution_time_seconds,
        'action_counts': action_counts,
        'safe_counts': safe_counts,
        'unsafe_counts': unsafe_counts,
        'has_unsafe_operations': ddl_plan.has_unsafe_operations
    }
    
    # Add interpretation
    if ddl_plan.has_unsafe_operations:
        summary['risk_level'] = "HIGH"
        summary['recommendation'] = "Review unsafe operations carefully, consider manual execution"
    elif len(ddl_plan.actions) > 10:
        summary['risk_level'] = "MEDIUM"
        summary['recommendation'] = "Consider batching operations"
    else:
        summary['risk_level'] = "LOW"
        summary['recommendation'] = "Safe to execute automatically"
    
    return summary
