"""
Analysis module for DataFrame-to-SQL alignment.

Performs comprehensive analysis of DataFrame vs table structure,
identifies issues, and generates alignment and DDL plans.
"""

import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from .models import (
    TableSpec, ColumnSpec, AnalysisReport, AlignmentPlan, DdlPlan, 
    DdlAction, Issue, IssueCode, Severity, ColumnMapping, OutlierResult
)
from .policies import AlignmentPolicies, DEFAULT_POLICIES
from .mapping import build_column_mapping, validate_mapping, infer_dtype_family
from .type_system import TypeFamily, is_type_compatible
from .outliers import detect_outliers


def analyze_dataframe_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyze DataFrame structure and content.
    
    Args:
        df: pandas DataFrame to analyze
        
    Returns:
        Dictionary with DataFrame analysis results
    """
    analysis = {
        'shape': df.shape,
        'columns': list(df.columns),
        'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()},
        'null_counts': df.isnull().sum().to_dict(),
        'memory_usage': df.memory_usage(deep=True).sum(),
        'sample_values': {}
    }
    
    # Sample values for each column (first 5 non-null values)
    for col in df.columns:
        non_null_values = df[col].dropna().head(5)
        analysis['sample_values'][col] = non_null_values.tolist()
    
    # Basic statistics for numeric columns
    numeric_cols = df.select_dtypes(include=['number']).columns
    if len(numeric_cols) > 0:
        analysis['numeric_stats'] = df[numeric_cols].describe().to_dict()
    
    # String length statistics
    string_cols = df.select_dtypes(include=['object']).columns
    if len(string_cols) > 0:
        analysis['string_lengths'] = {}
        for col in string_cols:
            col_data = df[col].dropna()
            if len(col_data) > 0:
                analysis['string_lengths'][col] = {
                    'max_length': col_data.astype(str).str.len().max(),
                    'min_length': col_data.astype(str).str.len().min(),
                    'avg_length': col_data.astype(str).str.len().mean()
                }
    
    return analysis


def validate_column_compatibility(
    df_col: str,
    df_analysis: Dict[str, Any],
    table_spec: TableSpec,
    policies: AlignmentPolicies
) -> List[Issue]:
    """
    Validate compatibility between DataFrame column and table column.
    
    Args:
        df_col: DataFrame column name
        df_analysis: DataFrame analysis results
        table_spec: Table specification
        policies: Alignment policies
        
    Returns:
        List of validation issues
    """
    issues = []
    
    # Find corresponding table column (assume exact match for now)
    table_col_spec = table_spec.get_column(df_col)
    if not table_col_spec:
        return issues
    
    df_dtype = df_analysis['dtypes'][df_col]
    df_null_count = df_analysis['null_counts'][df_col]
    df_type_family = infer_dtype_family(df_dtype)
    
    # Type compatibility
    if not is_type_compatible(df_type_family, table_col_spec.type_family, "oracle"):
        issues.append(Issue(
            code=IssueCode.COLUMN_TYPE_MISMATCH,
            severity=Severity.ERROR if policies.strict_mode else Severity.WARNING,
            message=f"Type mismatch: {df_dtype} ({getattr(df_type_family, 'value', df_type_family)}) -> {table_col_spec.sql_type} ({getattr(table_col_spec.type_family, 'value', table_col_spec.type_family)})",
            context={
                'df_dtype': df_dtype,
                'table_sql_type': table_col_spec.sql_type,
                'df_type_family': df_type_family.value,
                'table_type_family': getattr(table_col_spec.type_family, 'value', table_col_spec.type_family)
            },
            column_name=df_col
        ))
    
    # Nullability
    if not table_col_spec.nullable and df_null_count > 0:
        severity = Severity.ERROR if not policies.columns.allow_null_insert_for_not_null else Severity.WARNING
        issues.append(Issue(
            code=IssueCode.COLUMN_NULLABILITY_VIOLATION,
            severity=severity,
            message=f"Column '{df_col}' is NOT NULL but DataFrame has {df_null_count} null values",
            context={
                'null_count': df_null_count,
                'total_rows': df_analysis['shape'][0],
                'null_percentage': (df_null_count / df_analysis['shape'][0]) * 100
            },
            column_name=df_col
        ))
    
    # String length overflow
    if df_type_family == TypeFamily.STRING and table_col_spec.max_length:
        string_lengths = df_analysis.get('string_lengths', {}).get(df_col, {})
        max_df_length = string_lengths.get('max_length', 0)
        
        if max_df_length > table_col_spec.max_length:
            issues.append(Issue(
                code=IssueCode.COLUMN_LENGTH_OVERFLOW,
                severity=Severity.WARNING,
                message=f"String length overflow: max {max_df_length} > table limit {table_col_spec.max_length}",
                context={
                    'max_df_length': max_df_length,
                    'table_max_length': table_col_spec.max_length,
                    'overflow_amount': max_df_length - table_col_spec.max_length
                },
                column_name=df_col,
                sample_values=string_lengths.get('sample_values', [])
            ))
    
    # Numeric precision/scale overflow
    if df_type_family in (TypeFamily.INTEGER, TypeFamily.FLOAT, TypeFamily.DECIMAL):
        numeric_stats = df_analysis.get('numeric_stats', {}).get(df_col, {})
        if numeric_stats:
            max_value = numeric_stats.get('max', 0)
            min_value = numeric_stats.get('min', 0)
            
            # Check if values exceed reasonable bounds for the target type
            if table_col_spec.type_family == TypeFamily.INTEGER:
                # Assume standard integer bounds if not specified
                if table_col_spec.precision:
                    max_int_value = (10 ** table_col_spec.precision) - 1
                    if abs(max_value) > max_int_value or abs(min_value) > max_int_value:
                        issues.append(Issue(
                            code=IssueCode.COLUMN_PRECISION_OVERFLOW,
                            severity=Severity.WARNING,
                            message=f"Numeric precision overflow: values exceed {table_col_spec.precision} digits",
                            context={
                                'max_value': max_value,
                                'min_value': min_value,
                                'table_precision': table_col_spec.precision
                            },
                            column_name=df_col
                        ))
    
    # Datetime timezone issues
    if df_type_family == TypeFamily.DATETIME and table_col_spec.timezone is False:
        # DataFrame has timezone-aware datetime but table doesn't
        issues.append(Issue(
            code=IssueCode.DATETIME_TZ_MISMATCH,
            severity=Severity.INFO,
            message=f"Timezone handling: DataFrame datetime may have timezone but table column doesn't",
            context={
                'table_timezone': table_col_spec.timezone,
                'policy': policies.columns.datetime_tz_policy.value
            },
            column_name=df_col
        ))
    
    return issues


def analyze_constraints(
    df: pd.DataFrame,
    table_spec: TableSpec,
    policies: AlignmentPolicies
) -> List[Issue]:
    """
    Analyze constraint violations in DataFrame.
    
    Args:
        df: DataFrame to analyze
        table_spec: Table specification with constraints
        policies: Alignment policies
        
    Returns:
        List of constraint-related issues
    """
    issues = []
    
    if not policies.validation.check_constraints:
        return issues
    
    # Check primary key constraints
    for constraint_name, constraint in table_spec.constraints.items():
        if constraint.type == "PRIMARY_KEY":
            pk_columns = constraint.columns
            
            # Check for null values in PK columns
            for col in pk_columns:
                if col in df.columns and df[col].isnull().any():
                    issues.append(Issue(
                        code=IssueCode.CONSTRAINT_VIOLATION,
                        severity=Severity.ERROR,
                        message=f"Primary key column '{col}' contains null values",
                        context={'constraint': constraint_name, 'column': col},
                        column_name=col
                    ))
            
            # Check for duplicates in PK columns
            if all(col in df.columns for col in pk_columns):
                pk_data = df[pk_columns]
                duplicates = pk_data.duplicated()
                if duplicates.any():
                    duplicate_count = duplicates.sum()
                    issues.append(Issue(
                        code=IssueCode.CONSTRAINT_VIOLATION,
                        severity=Severity.ERROR,
                        message=f"Primary key constraint violation: {duplicate_count} duplicate rows",
                        context={
                            'constraint': constraint_name,
                            'columns': pk_columns,
                            'duplicate_count': duplicate_count
                        }
                    ))
        
        # Check unique constraints
        elif constraint.type == "UNIQUE":
            unique_columns = constraint.columns
            
            if all(col in df.columns for col in unique_columns):
                unique_data = df[unique_columns]
                duplicates = unique_data.duplicated()
                if duplicates.any():
                    duplicate_count = duplicates.sum()
                    issues.append(Issue(
                        code=IssueCode.CONSTRAINT_VIOLATION,
                        severity=Severity.ERROR,
                        message=f"Unique constraint violation: {duplicate_count} duplicate rows",
                        context={
                            'constraint': constraint_name,
                            'columns': unique_columns,
                            'duplicate_count': duplicate_count
                        }
                    ))
        
        # Check foreign key constraints (simplified - would need reference data)
        elif constraint.type == "FOREIGN_KEY":
            fk_columns = constraint.columns
            
            for col in fk_columns:
                if col in df.columns and df[col].isnull().any():
                    # This is only an issue if the FK column is NOT NULL
                    table_col = table_spec.get_column(col)
                    if table_col and not table_col.nullable:
                        issues.append(Issue(
                            code=IssueCode.REFERENTIAL_INTEGRITY_VIOLATION,
                            severity=Severity.WARNING,
                            message=f"Foreign key column '{col}' contains null values",
                            context={'constraint': constraint_name, 'column': col},
                            column_name=col
                        ))
    
    return issues


def generate_alignment_plan(
    mappings: List[ColumnMapping],
    extra_df_columns: List[str],
    missing_db_columns: List[str],
    issues: List[Issue],
    policies: AlignmentPolicies
) -> AlignmentPlan:
    """
    Generate alignment plan based on analysis results.
    
    Args:
        mappings: Column mappings
        extra_df_columns: Extra DataFrame columns
        missing_db_columns: Missing database columns
        issues: Analysis issues
        policies: Alignment policies
        
    Returns:
        Alignment plan
    """
    plan = AlignmentPlan()
    
    # Handle extra DataFrame columns based on policy
    if policies.columns.extra_df_columns_action.value == "drop":
        plan.drop_columns = extra_df_columns.copy()
        for col in extra_df_columns:
            plan.column_actions[col] = "drop"
    elif policies.columns.extra_df_columns_action.value == "keep":
        for col in extra_df_columns:
            plan.column_actions[col] = "keep"
    elif policies.columns.extra_df_columns_action.value == "error":
        for col in extra_df_columns:
            plan.column_actions[col] = "error"
    
    # Handle mapped columns
    for mapping in mappings:
        plan.column_actions[mapping.df_column] = "map"
        plan.transformations[mapping.df_column] = mapping.transformation
    
    # Handle missing DB columns (informational only)
    for col in missing_db_columns:
        table_col_spec = None
        # Find the table column spec
        for spec in mappings:
            if spec.table_column == col:
                # This shouldn't happen in missing_db_columns, but just in case
                break
        
        plan.column_actions[col] = "missing_in_df"
    
    # Handle outlier action
    if policies.outliers.enabled:
        plan.outlier_action = policies.outliers.action.value
    
    return plan


def generate_ddl_plan(
    table_spec: TableSpec,
    missing_db_columns: List[str],
    issues: List[Issue],
    policies: AlignmentPolicies
) -> DdlPlan:
    """
    Generate DDL plan for schema updates.
    
    Args:
        table_spec: Current table specification
        missing_db_columns: Columns present in table but missing from DataFrame
        issues: Analysis issues
        policies: Alignment policies
        
    Returns:
        DDL plan
    """
    plan = DdlPlan()
    
    if not policies.ddl.enabled:
        return plan
    
    # Note: This is a simplified DDL plan generation
    # In a full implementation, we would analyze what columns need to be added,
    # widened, or have their types changed based on DataFrame analysis
    
    # For now, we only handle safe operations like adding columns
    if policies.ddl.allow_add_columns:
        # This would be for columns that should exist in the table but don't
        # (not covered in current analysis which focuses on DF->table alignment)
        pass
    
    return plan


def analyze(
    df: pd.DataFrame,
    table_spec: TableSpec,
    policies: Optional[AlignmentPolicies] = None
) -> Tuple[AnalysisReport, AlignmentPlan, DdlPlan]:
    """
    Perform comprehensive analysis of DataFrame vs table structure.
    
    Args:
        df: DataFrame to analyze
        table_spec: Target table specification
        policies: Alignment policies (uses defaults if None)
        
    Returns:
        Tuple of (AnalysisReport, AlignmentPlan, DdlPlan)
    """
    if policies is None:
        policies = DEFAULT_POLICIES
    
    # Analyze DataFrame structure
    df_analysis = analyze_dataframe_structure(df)
    
    # Build column mappings
    mappings, extra_df_columns, missing_db_columns = build_column_mapping(
        df_analysis['columns'],
        table_spec,
        dialect="oracle",  # Default to Oracle as per plan
        confidence_threshold=policies.validation.confidence_threshold
    )
    
    # Collect all issues
    all_issues = []
    
    # Validate column compatibility for mapped columns
    for mapping in mappings:
        col_issues = validate_column_compatibility(
            mapping.df_column,
            df_analysis,
            table_spec,
            policies
        )
        all_issues.extend(col_issues)
    
    # Add issues for extra DataFrame columns
    for col in extra_df_columns:
        if policies.columns.extra_df_columns_action.value == "error":
            all_issues.append(Issue(
                code=IssueCode.EXTRA_DF_COLUMN,
                severity=Severity.ERROR,
                message=f"Extra DataFrame column '{col}' not found in table",
                column_name=col
            ))
        else:
            all_issues.append(Issue(
                code=IssueCode.EXTRA_DF_COLUMN,
                severity=Severity.INFO,
                message=f"Extra DataFrame column '{col}' will be {policies.columns.extra_df_columns_action.value}d",
                column_name=col
            ))
    
    # Add issues for missing DB columns
    for col in missing_db_columns:
        table_col_spec = table_spec.get_column(col)
        if table_col_spec and not table_col_spec.nullable and not table_col_spec.default_value:
            all_issues.append(Issue(
                code=IssueCode.MISSING_DB_COLUMN,
                severity=Severity.ERROR,
                message=f"Required table column '{col}' missing from DataFrame and has no default",
                column_name=col
            ))
        else:
            all_issues.append(Issue(
                code=IssueCode.MISSING_DB_COLUMN,
                severity=Severity.WARNING,
                message=f"Table column '{col}' missing from DataFrame",
                column_name=col
            ))
    
    # Analyze constraints
    constraint_issues = analyze_constraints(df, table_spec, policies)
    all_issues.extend(constraint_issues)
    
    # Outlier detection (if enabled)
    outlier_result = None
    if policies.outliers.enabled:
        # Get numeric columns for outlier detection
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        if numeric_cols:
            outlier_result = detect_outliers(
                df,
                numeric_cols,
                method=policies.outliers.method,
                combine_rule=policies.outliers.combine_rule,
                iqr_factor=policies.outliers.iqr_factor,
                mad_factor=policies.outliers.mad_factor,
                zscore_threshold=policies.outliers.zscore_threshold
            )
            
            # Check if outlier rate exceeds cap
            if outlier_result.outlier_percentage > (policies.outliers.max_pct_total_rows * 100):
                all_issues.append(Issue(
                    code=IssueCode.OUTLIER_RATE_EXCEEDS_CAP,
                    severity=Severity.ERROR,
                    message=f"Outlier rate {outlier_result.outlier_percentage:.1f}% exceeds cap {policies.outliers.max_pct_total_rows * 100}%",
                    context={
                        'outlier_percentage': outlier_result.outlier_percentage,
                        'max_percentage': policies.outliers.max_pct_total_rows * 100,
                        'outlier_count': outlier_result.outlier_rows,
                        'total_rows': outlier_result.total_rows
                    }
                ))
            else:
                all_issues.append(Issue(
                    code=IssueCode.OUTLIERS_DETECTED,
                    severity=Severity.INFO,
                    message=f"Detected {outlier_result.outlier_rows} outliers ({outlier_result.outlier_percentage:.1f}%)",
                    context={
                        'outlier_count': outlier_result.outlier_rows,
                        'total_rows': outlier_result.total_rows,
                        'outlier_percentage': outlier_result.outlier_percentage
                    }
                ))
    
    # Calculate confidence score
    total_columns = len(df_analysis['columns'])
    mapped_columns = len(mappings)
    confidence_score = mapped_columns / total_columns if total_columns > 0 else 0.0
    
    # Create analysis report
    report = AnalysisReport(
        table_spec=table_spec,
        column_mappings=mappings,
        issues=all_issues,
        extra_df_columns=extra_df_columns,
        missing_db_columns=missing_db_columns,
        outlier_result=outlier_result,
        total_columns=total_columns,
        mapped_columns=mapped_columns,
        confidence_score=confidence_score
    )
    
    # Generate plans
    alignment_plan = generate_alignment_plan(
        mappings, extra_df_columns, missing_db_columns, all_issues, policies
    )
    
    ddl_plan = generate_ddl_plan(table_spec, missing_db_columns, all_issues, policies)
    
    return report, alignment_plan, ddl_plan
