"""
DataFrame coercion for SQL alignment.

Handles type conversion, data validation, and transformation of DataFrame
to match target table structure according to alignment policies.
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from .models import (
    TableSpec, ColumnSpec, CoercionReport, AlignmentPlan, 
    Issue, IssueCode, Severity
)
from .policies import AlignmentPolicies, DEFAULT_POLICIES
from .type_system import TypeFamily, is_type_compatible
from .outliers import apply_outlier_action, OutlierResult


def coerce_string_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce string column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    issues = []
    result = series.copy()
    
    # Convert to string
    result = result.astype(str)
    
    # Handle length constraints
    if table_spec.max_length:
        max_length = table_spec.max_length
        
        # Check for overflow
        lengths = result.str.len()
        overflow_mask = lengths > max_length
        
        if overflow_mask.any():
            overflow_count = overflow_mask.sum()
            
            if policies.columns.string_overflow_action.value == "truncate":
                result = result.str.slice(0, max_length)
                issues.append(f"Truncated {overflow_count} string values to {max_length} characters")
            elif policies.columns.string_overflow_action.value == "error":
                issues.append(f"String length overflow: {overflow_count} values exceed {max_length} characters")
    
    # Handle null values
    if not table_spec.nullable:
        null_mask = result.isnull()
        if null_mask.any():
            null_count = null_mask.sum()
            if policies.columns.allow_null_insert_for_not_null:
                # Fill with empty string
                result = result.fillna("")
                issues.append(f"Filled {null_count} null values with empty string for NOT NULL column")
            else:
                issues.append(f"Column has {null_count} null values but is NOT NULL")
    
    return result, issues


def coerce_numeric_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce numeric column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    issues = []
    result = series.copy()
    
    # Handle boolean to numeric conversion
    if result.dtype == 'bool':
        if policies.columns.bool_to_int:
            result = result.astype(int)
            issues.append("Converted boolean values to integers (0/1)")
        else:
            result = result.astype(float)
    
    # Convert to appropriate numeric type
    if table_spec.type_family == TypeFamily.INTEGER:
        try:
            result = pd.to_numeric(result, errors='coerce')
            # Round to integer
            result = result.round()
            result = result.astype('Int64')  # Nullable integer
        except Exception as e:
            issues.append(f"Failed to convert to integer: {str(e)}")
    
    elif table_spec.type_family == TypeFamily.FLOAT:
        try:
            result = pd.to_numeric(result, errors='coerce')
            result = result.astype('float64')
        except Exception as e:
            issues.append(f"Failed to convert to float: {str(e)}")
    
    elif table_spec.type_family == TypeFamily.DECIMAL:
        try:
            result = pd.to_numeric(result, errors='coerce')
            # Apply precision/scale constraints
            if table_spec.precision and table_spec.scale is not None:
                # Round to specified scale
                result = result.round(table_spec.scale)
            result = result.astype('object')  # Keep as object for decimal precision
        except Exception as e:
            issues.append(f"Failed to convert to decimal: {str(e)}")
    
    # Handle precision overflow
    if table_spec.precision and table_spec.type_family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
        max_value = 10 ** table_spec.precision - 1
        min_value = -(10 ** table_spec.precision - 1)
        
        overflow_mask = (result > max_value) | (result < min_value)
        if overflow_mask.any():
            overflow_count = overflow_mask.sum()
            
            if policies.columns.numeric_overflow_action.value == "round":
                # Clip to bounds
                result = result.clip(min_value, max_value)
                issues.append(f"Clipped {overflow_count} numeric values to fit precision {table_spec.precision}")
            elif policies.columns.numeric_overflow_action.value == "error":
                issues.append(f"Numeric precision overflow: {overflow_count} values exceed precision {table_spec.precision}")
    
    # Handle null values
    if not table_spec.nullable:
        null_mask = result.isnull()
        if null_mask.any():
            null_count = null_mask.sum()
            if policies.columns.allow_null_insert_for_not_null:
                # Fill with 0 for numeric columns
                result = result.fillna(0)
                issues.append(f"Filled {null_count} null values with 0 for NOT NULL numeric column")
            else:
                issues.append(f"Numeric column has {null_count} null values but is NOT NULL")
    
    return result, issues


def coerce_datetime_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce datetime column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    issues = []
    result = series.copy()
    
    # Convert to datetime
    try:
        if result.dtype == 'object':
            result = pd.to_datetime(result, errors='coerce')
        elif pd.api.types.is_numeric_dtype(result):
            # Convert numeric timestamps
            result = pd.to_datetime(result, unit='s', errors='coerce')
    except Exception as e:
        issues.append(f"Failed to convert to datetime: {str(e)}")
        return result, issues
    
    # Handle timezone policy
    if policies.columns.datetime_tz_policy.value == "assume_utc":
        if result.dt.tz is None:
            result = result.dt.tz_localize('UTC')
        else:
            result = result.dt.tz_convert('UTC')
    elif policies.columns.datetime_tz_policy.value == "assume_local":
        if result.dt.tz is None:
            result = result.dt.tz_localize('local')
    
    # Remove timezone if table column doesn't support it
    if not table_spec.timezone and result.dt.tz is not None:
        result = result.dt.tz_localize(None)
        issues.append("Removed timezone information for non-timezone column")
    
    # Handle null values
    if not table_spec.nullable:
        null_mask = result.isnull()
        if null_mask.any():
            null_count = null_mask.sum()
            if policies.columns.allow_null_insert_for_not_null:
                # Fill with current datetime
                result = result.fillna(pd.Timestamp.now())
                issues.append(f"Filled {null_count} null datetime values with current timestamp")
            else:
                issues.append(f"Datetime column has {null_count} null values but is NOT NULL")
    
    return result, issues


def coerce_boolean_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce boolean column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    issues = []
    result = series.copy()
    
    # Convert various boolean representations
    if result.dtype == 'object':
        # Handle string representations
        result_lower = result.str.lower() if result.dtype == 'object' else result
        
        # Common boolean string patterns
        true_values = ['true', 't', 'yes', 'y', '1', 'on']
        false_values = ['false', 'f', 'no', 'n', '0', 'off']
        
        result = result_lower.isin(true_values)
        # Handle false values explicitly
        result = result.where(~result_lower.isin(false_values), False)
    
    # Convert to boolean
    try:
        result = result.astype('boolean')
    except Exception as e:
        issues.append(f"Failed to convert to boolean: {str(e)}")
        return result, issues
    
    # Handle null values
    if not table_spec.nullable:
        null_mask = result.isnull()
        if null_mask.any():
            null_count = null_mask.sum()
            if policies.columns.allow_null_insert_for_not_null:
                result = result.fillna(False)
                issues.append(f"Filled {null_count} null boolean values with False")
            else:
                issues.append(f"Boolean column has {null_count} null values but is NOT NULL")
    
    return result, issues


def coerce_json_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce JSON column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    issues = []
    result = series.copy()
    
    # Convert to string for JSON storage
    if result.dtype != 'object':
        result = result.astype(str)
    
    # Validate JSON format
    def validate_json(value):
        if pd.isna(value):
            return value
        try:
            json.loads(value)
            return value
        except (json.JSONDecodeError, TypeError):
            # Convert to JSON string
            return json.dumps(value)
    
    result = result.apply(validate_json)
    
    # Handle null values
    if not table_spec.nullable:
        null_mask = result.isnull()
        if null_mask.any():
            null_count = null_mask.sum()
            if policies.columns.allow_null_insert_for_not_null:
                result = result.fillna('{}')
                issues.append(f"Filled {null_count} null JSON values with empty object")
            else:
                issues.append(f"JSON column has {null_count} null values but is NOT NULL")
    
    return result, issues


def coerce_binary_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce binary column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    issues = []
    result = series.copy()
    
    # Convert to bytes
    if result.dtype == 'object':
        # Try to convert string to bytes
        try:
            result = result.apply(lambda x: x.encode('utf-8') if isinstance(x, str) else x)
        except Exception as e:
            issues.append(f"Failed to convert strings to bytes: {str(e)}")
    
    # Handle length constraints
    if table_spec.max_length:
        def truncate_bytes(value):
            if pd.isna(value):
                return value
            if isinstance(value, bytes) and len(value) > table_spec.max_length:
                return value[:table_spec.max_length]
            return value
        
        result = result.apply(truncate_bytes)
    
    # Handle null values
    if not table_spec.nullable:
        null_mask = result.isnull()
        if null_mask.any():
            null_count = null_mask.sum()
            if policies.columns.allow_null_insert_for_not_null:
                result = result.fillna(b'')
                issues.append(f"Filled {null_count} null binary values with empty bytes")
            else:
                issues.append(f"Binary column has {null_count} null values but is NOT NULL")
    
    return result, issues


def coerce_column(
    series: pd.Series,
    table_spec: ColumnSpec,
    policies: AlignmentPolicies
) -> Tuple[pd.Series, List[str]]:
    """
    Coerce a single column to match table specifications.
    
    Args:
        series: pandas Series to coerce
        table_spec: Target column specification
        policies: Alignment policies
        
    Returns:
        Tuple of (coerced series, list of issues)
    """
    # Route to appropriate coercion function based on type family.
    # ColumnSpec.type_family stores the string value (e.g. "string"),
    # so we normalise to the enum for safe comparison.
    family = (
        table_spec.type_family
        if isinstance(table_spec.type_family, TypeFamily)
        else TypeFamily(table_spec.type_family)
    )
    if family == TypeFamily.STRING:
        return coerce_string_column(series, table_spec, policies)
    elif family in (TypeFamily.INTEGER, TypeFamily.FLOAT, TypeFamily.DECIMAL):
        return coerce_numeric_column(series, table_spec, policies)
    elif family == TypeFamily.DATETIME:
        return coerce_datetime_column(series, table_spec, policies)
    elif family == TypeFamily.BOOLEAN:
        return coerce_boolean_column(series, table_spec, policies)
    elif family == TypeFamily.JSON:
        return coerce_json_column(series, table_spec, policies)
    elif family == TypeFamily.BINARY:
        return coerce_binary_column(series, table_spec, policies)
    else:
        # Default handling
        issues = [f"Unknown type family: {table_spec.type_family}"]
        return series, issues


def coerce_dataframe(
    df: pd.DataFrame,
    table_spec: TableSpec,
    alignment_plan: AlignmentPlan,
    policies: Optional[AlignmentPolicies] = None,
    outlier_result: Optional[OutlierResult] = None
) -> Tuple[pd.DataFrame, CoercionReport]:
    """
    Coerce DataFrame to match table structure according to alignment plan.
    
    Args:
        df: Original DataFrame
        table_spec: Target table specification
        alignment_plan: Plan for column alignment
        policies: Alignment policies (uses defaults if None)
        outlier_result: Optional outlier detection result
        
    Returns:
        Tuple of (coerced DataFrame, coercion report)
    """
    if policies is None:
        policies = DEFAULT_POLICIES
    
    original_shape = df.shape
    df_result = df.copy()
    
    # Track coercion statistics
    dropped_columns = []
    transformed_columns = []
    column_stats = {}
    all_issues = []
    
    # Step 1: Apply outlier correction if enabled and within cap
    if (policies.outliers.enabled and 
        outlier_result and 
        outlier_result.outlier_percentage <= (policies.outliers.max_pct_total_rows * 100)):
        
        df_result, outlier_details = apply_outlier_action(
            df_result, outlier_result, policies.outliers.action.value
        )
        
        dropped_rows = original_shape[0] - df_result.shape[0]
        if dropped_rows > 0:
            all_issues.append(f"Applied outlier correction: {policies.outliers.action.value} {dropped_rows} rows")
    else:
        dropped_rows = 0
    
    # Step 2: Drop extra DataFrame columns according to policy
    for col in alignment_plan.drop_columns:
        if col in df_result.columns:
            df_result = df_result.drop(columns=[col])
            dropped_columns.append(col)
            all_issues.append(f"Dropped extra column: {col}")
    
    # Step 3: Coerce mapped columns (column_actions maps df_col -> action)
    for df_col, action in alignment_plan.column_actions.items():
        if action != "map":
            continue
        if df_col not in df_result.columns:
            continue
        
        # Resolve table column via transformations or same name
        table_col = alignment_plan.transformations.get(df_col) or df_col
        table_col_spec = table_spec.get_column(table_col)
        if not table_col_spec:
            # Try same name directly
            table_col_spec = table_spec.get_column(df_col)
        if not table_col_spec:
            continue
        
        original_series = df_result[df_col]
        coerced_series, issues = coerce_column(original_series, table_col_spec, policies)
        
        df_result[df_col] = coerced_series
        
        if not original_series.equals(coerced_series):
            transformed_columns.append(df_col)
        
        all_issues.extend(issues)
        
        column_stats[df_col] = {
            'original_dtype': str(original_series.dtype),
            'final_dtype': str(coerced_series.dtype),
            'null_count_before': original_series.isnull().sum(),
            'null_count_after': coerced_series.isnull().sum(),
            'unique_values_before': original_series.nunique(),
            'unique_values_after': coerced_series.nunique(),
            'issues': issues
        }
    
    # Create coercion report
    report = CoercionReport(
        original_shape=original_shape,
        final_shape=df_result.shape,
        dropped_rows=dropped_rows,
        dropped_columns=dropped_columns,
        transformed_columns=transformed_columns,
        issues=[Issue(
            code=IssueCode.CONVERSION_ERROR,
            severity=Severity.WARNING,
            message=issue
        ) for issue in all_issues],
        column_stats=column_stats
    )
    
    return df_result, report
