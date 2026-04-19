"""
Outlier detection and correction for DataFrame-to-SQL alignment.

Implements multiple outlier detection methods with configurable actions
and strict percentage caps to prevent excessive data loss.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from .models import OutlierResult


def detect_outliers_iqr(
    df: pd.DataFrame,
    columns: List[str],
    factor: float = 1.5
) -> pd.Series:
    """
    Detect outliers using Interquartile Range (IQR) method.
    
    Args:
        df: DataFrame to analyze
        columns: List of column names to check
        factor: IQR factor (default 1.5)
        
    Returns:
        Boolean Series indicating outlier rows
    """
    outlier_mask = pd.Series(False, index=df.index)
    
    for col in columns:
        if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        
        col_data = df[col].dropna()
        if len(col_data) == 0:
            continue
        
        Q1 = col_data.quantile(0.25)
        Q3 = col_data.quantile(0.75)
        IQR = Q3 - Q1
        
        lower_bound = Q1 - factor * IQR
        upper_bound = Q3 + factor * IQR
        
        col_outliers = (df[col] < lower_bound) | (df[col] > upper_bound)
        outlier_mask = outlier_mask | col_outliers
    
    return outlier_mask


def detect_outliers_mad(
    df: pd.DataFrame,
    columns: List[str],
    factor: float = 3.0
) -> pd.Series:
    """
    Detect outliers using Median Absolute Deviation (MAD) method.
    
    Args:
        df: DataFrame to analyze
        columns: List of column names to check
        factor: MAD factor (default 3.0)
        
    Returns:
        Boolean Series indicating outlier rows
    """
    outlier_mask = pd.Series(False, index=df.index)
    
    for col in columns:
        if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        
        col_data = df[col].dropna()
        if len(col_data) == 0:
            continue
        
        median = col_data.median()
        mad = np.median(np.abs(col_data - median))
        
        if mad == 0:
            continue
        
        modified_z_scores = 0.6745 * (df[col] - median) / mad
        col_outliers = np.abs(modified_z_scores) > factor
        outlier_mask = outlier_mask | col_outliers
    
    return outlier_mask


def detect_outliers_zscore(
    df: pd.DataFrame,
    columns: List[str],
    threshold: float = 3.0
) -> pd.Series:
    """
    Detect outliers using Z-score method.
    
    Args:
        df: DataFrame to analyze
        columns: List of column names to check
        threshold: Z-score threshold (default 3.0)
        
    Returns:
        Boolean Series indicating outlier rows
    """
    outlier_mask = pd.Series(False, index=df.index)
    
    for col in columns:
        if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        
        col_data = df[col].dropna()
        if len(col_data) == 0:
            continue
        
        mean = col_data.mean()
        std = col_data.std()
        
        if std == 0:
            continue
        
        z_scores = np.abs((df[col] - mean) / std)
        col_outliers = z_scores > threshold
        outlier_mask = outlier_mask | col_outliers
    
    return outlier_mask


def detect_outliers(
    df: pd.DataFrame,
    columns: List[str],
    method: str = "iqr",
    combine_rule: str = "any",
    iqr_factor: float = 1.5,
    mad_factor: float = 3.0,
    zscore_threshold: float = 3.0
) -> OutlierResult:
    """
    Detect outliers in DataFrame using specified method.
    
    Args:
        df: DataFrame to analyze
        columns: List of column names to check
        method: Detection method ("iqr", "mad", "zscore")
        combine_rule: How to combine multi-column outliers ("any", "all")
        iqr_factor: IQR factor for IQR method
        mad_factor: MAD factor for MAD method
        zscore_threshold: Z-score threshold for Z-score method
        
    Returns:
        OutlierResult with detection results
    """
    total_rows = len(df)
    
    if method == "iqr":
        outlier_mask = detect_outliers_iqr(df, columns, iqr_factor)
    elif method == "mad":
        outlier_mask = detect_outliers_mad(df, columns, mad_factor)
    elif method == "zscore":
        outlier_mask = detect_outliers_zscore(df, columns, zscore_threshold)
    else:
        raise ValueError(f"Unknown outlier detection method: {method}")
    
    # For multiple columns, apply combine rule
    if len(columns) > 1:
        if combine_rule == "any":
            final_outlier_mask = outlier_mask
        elif combine_rule == "all":
            # For "all" rule, we need to detect outliers per column first
            column_masks = []
            for col in columns:
                if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
                    continue
                
                if method == "iqr":
                    col_mask = detect_outliers_iqr(df, [col], iqr_factor)
                elif method == "mad":
                    col_mask = detect_outliers_mad(df, [col], mad_factor)
                elif method == "zscore":
                    col_mask = detect_outliers_zscore(df, [col], zscore_threshold)
                
                column_masks.append(col_mask)
            
            if column_masks:
                final_outlier_mask = pd.Series(True, index=df.index)
                for mask in column_masks:
                    final_outlier_mask = final_outlier_mask & mask
            else:
                final_outlier_mask = pd.Series(False, index=df.index)
        else:
            raise ValueError(f"Unknown combine rule: {combine_rule}")
    else:
        final_outlier_mask = outlier_mask
    
    outlier_indices = df[final_outlier_mask].index.tolist()
    outlier_rows = len(outlier_indices)
    
    # Calculate per-column statistics
    column_details = {}
    for col in columns:
        if col not in df.columns or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        
        col_outlier_mask = final_outlier_mask
        col_outlier_count = col_outlier_mask.sum()
        
        column_details[col] = {
            'outlier_count': int(col_outlier_count),
            'outlier_percentage': (col_outlier_count / total_rows) * 100 if total_rows > 0 else 0.0,
            'sample_values': df.loc[col_outlier_mask, col].head(5).tolist()
        }
    
    return OutlierResult(
        total_rows=total_rows,
        outlier_rows=outlier_rows,
        outlier_indices=outlier_indices,
        outlier_columns=columns,
        method=method,
        details={
            'combine_rule': combine_rule,
            'method_params': {
                'iqr_factor': iqr_factor,
                'mad_factor': mad_factor,
                'zscore_threshold': zscore_threshold
            },
            'column_details': column_details
        }
    )


def apply_outlier_action(
    df: pd.DataFrame,
    outlier_result: OutlierResult,
    action: str = "drop"
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Apply outlier correction action to DataFrame.
    
    Args:
        df: Original DataFrame
        outlier_result: Result from outlier detection
        action: Action to apply ("drop", "nullify", "clip")
        
    Returns:
        Tuple of (modified DataFrame, action details)
    """
    df_copy = df.copy()
    outlier_mask = pd.Series(False, index=df_copy.index)
    outlier_mask.loc[outlier_result.outlier_indices] = True
    
    action_details = {
        'action': action,
        'original_shape': df.shape,
        'outlier_indices': outlier_result.outlier_indices,
        'affected_columns': outlier_result.outlier_columns
    }
    
    if action == "drop":
        df_cleaned = df_copy[~outlier_mask]
        action_details['final_shape'] = df_cleaned.shape
        action_details['dropped_rows'] = len(outlier_result.outlier_indices)
        
    elif action == "nullify":
        df_cleaned = df_copy.copy()
        # Set outliers to NaN in their respective columns
        for col in outlier_result.outlier_columns:
            if col in df_cleaned.columns:
                df_cleaned.loc[outlier_mask, col] = np.nan
        
        action_details['final_shape'] = df_cleaned.shape
        action_details['nullified_cells'] = 0
        for col in outlier_result.outlier_columns:
            if col in df_cleaned.columns:
                action_details['nullified_cells'] += outlier_mask.sum()
        
    elif action == "clip":
        df_cleaned = df_copy.copy()
        clipped_count = 0
        
        # For each column, clip outliers to reasonable bounds
        for col in outlier_result.outlier_columns:
            if col not in df_cleaned.columns or not pd.api.types.is_numeric_dtype(df_cleaned[col]):
                continue
            
            col_data = df_cleaned[col].dropna()
            if len(col_data) == 0:
                continue
            
            # Calculate bounds based on method used
            if outlier_result.method == "iqr":
                iqr_factor = outlier_result.details['method_params']['iqr_factor']
                Q1 = col_data.quantile(0.25)
                Q3 = col_data.quantile(0.75)
                IQR = Q3 - Q1
                lower_bound = Q1 - iqr_factor * IQR
                upper_bound = Q3 + iqr_factor * IQR
            elif outlier_result.method == "mad":
                mad_factor = outlier_result.details['method_params']['mad_factor']
                median = col_data.median()
                mad = np.median(np.abs(col_data - median))
                if mad > 0:
                    lower_bound = median - mad_factor * mad / 0.6745
                    upper_bound = median + mad_factor * mad / 0.6745
                else:
                    continue
            elif outlier_result.method == "zscore":
                zscore_threshold = outlier_result.details['method_params']['zscore_threshold']
                mean = col_data.mean()
                std = col_data.std()
                if std > 0:
                    lower_bound = mean - zscore_threshold * std
                    upper_bound = mean + zscore_threshold * std
                else:
                    continue
            else:
                continue
            
            # Apply clipping
            original_values = df_cleaned.loc[outlier_mask, col].copy()
            df_cleaned.loc[outlier_mask, col] = df_cleaned.loc[outlier_mask, col].clip(lower_bound, upper_bound)
            
            # Count how many values were actually changed
            changed_count = (original_values != df_cleaned.loc[outlier_mask, col]).sum()
            clipped_count += changed_count
        
        action_details['final_shape'] = df_cleaned.shape
        action_details['clipped_values'] = clipped_count
        
    else:
        raise ValueError(f"Unknown outlier action: {action}")
    
    return df_cleaned, action_details


def validate_outlier_parameters(
    method: str,
    combine_rule: str,
    iqr_factor: float,
    mad_factor: float,
    zscore_threshold: float
) -> None:
    """
    Validate outlier detection parameters.
    
    Args:
        method: Detection method
        combine_rule: Combine rule for multiple columns
        iqr_factor: IQR factor
        mad_factor: MAD factor
        zscore_threshold: Z-score threshold
        
    Raises:
        ValueError: If parameters are invalid
    """
    valid_methods = ["iqr", "mad", "zscore"]
    if method not in valid_methods:
        raise ValueError(f"Method must be one of {valid_methods}, got: {method}")
    
    valid_combine_rules = ["any", "all"]
    if combine_rule not in valid_combine_rules:
        raise ValueError(f"Combine rule must be one of {valid_combine_rules}, got: {combine_rule}")
    
    if iqr_factor <= 0:
        raise ValueError("IQR factor must be positive")
    
    if mad_factor <= 0:
        raise ValueError("MAD factor must be positive")
    
    if zscore_threshold <= 0:
        raise ValueError("Z-score threshold must be positive")


def get_outlier_summary(outlier_result: OutlierResult) -> Dict[str, Any]:
    """
    Get a comprehensive summary of outlier detection results.
    
    Args:
        outlier_result: Result from outlier detection
        
    Returns:
        Summary dictionary
    """
    summary = {
        'total_rows': outlier_result.total_rows,
        'outlier_rows': outlier_result.outlier_rows,
        'outlier_percentage': outlier_result.outlier_percentage,
        'method': outlier_result.method,
        'columns_checked': outlier_result.outlier_columns,
        'combine_rule': outlier_result.details.get('combine_rule', 'any'),
        'method_params': outlier_result.details.get('method_params', {}),
        'column_details': outlier_result.details.get('column_details', {})
    }
    
    # Add interpretation
    if outlier_result.outlier_percentage == 0:
        summary['interpretation'] = "No outliers detected"
    elif outlier_result.outlier_percentage < 1:
        summary['interpretation'] = "Very few outliers detected"
    elif outlier_result.outlier_percentage < 5:
        summary['interpretation'] = "Moderate number of outliers detected"
    elif outlier_result.outlier_percentage < 10:
        summary['interpretation'] = "Many outliers detected"
    else:
        summary['interpretation'] = "Very many outliers detected - review data quality"
    
    return summary
