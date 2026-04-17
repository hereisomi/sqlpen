import pandas as pd
import numpy as np
import re
from typing import Tuple, Dict, Optional

def cast_df(df, dtype=None):

    PATTERNS = {
        'timestamp': [r'^\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}', r'^\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}', r'^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}'],
        'date': [r'^\d{4}-\d{2}-\d{2}$', r'^\d{2}/\d{2}/\d{4}$', r'^\d{4}/\d{2}/\d{2}$', r'^\d{2}-\d{2}-\d{4}$'],
        'time': [r'^\d{2}:\d{2}:\d{2}$', r'^\d{2}:\d{2}:\d{2}\.\d+$', r'^\d{2}:\d{2}$'],
        'duration': [r'^\d+:\d{2}:\d{2}$', r'^\d+\s*(hours?|hrs?|h)\s*\d*\s*(minutes?|mins?|m)?', r'^\d+\s*days?\s*\d*:\d{2}:\d{2}'],
        'boolean': [r'^(true|false)$', r'^(yes|no)$', r'^(y|n)$', r'^(t|f)$'],
        'binary': [r'^[01]$'],
        'integer': [r'^-?\d+$'],
        'float': [r'^-?\d+\.\d+$', r'^-?\d+\.?\d*e[+-]?\d+$'],
    }
    
    def identify_datatype(values):
        if len(values) == 0: return 'string'
        str_values = [str(v).strip().lower() for v in values]
        for dtype_name, patterns in PATTERNS.items():
            if sum(any(re.match(p, v, re.IGNORECASE) for p in patterns) for v in str_values) == len(str_values):
                return dtype_name
        return 'string'
    
    def convert_column(series, target_dtype):
        try:
            if target_dtype == 'timestamp': return pd.to_datetime(series, errors='coerce')
            elif target_dtype == 'date': return pd.to_datetime(series, errors='coerce').dt.date
            elif target_dtype == 'time': return pd.to_datetime(series, format='%H:%M:%S', errors='coerce').dt.time
            elif target_dtype == 'duration': return pd.to_timedelta(series, errors='coerce')
            elif target_dtype == 'boolean':
                bool_map = {'true': True, 'false': False, 'yes': True, 'no': False, 'y': True, 'n': False, 't': True, 'f': False, '1': True, '0': False}
                return series.str.lower().str.strip().map(bool_map)
            elif target_dtype == 'binary': return pd.to_numeric(series, errors='coerce').astype('Int64')
            elif target_dtype == 'integer': return pd.to_numeric(series, errors='coerce').astype('Int64')
            elif target_dtype == 'float': return pd.to_numeric(series, errors='coerce').astype('float64')
            else: return series
        except Exception as e:
            print(f"Warning: Conversion failed with error: {e}")
            return series
    
    def convert_explicit_dtype(series, target_dtype):
        try:
            if 'datetime' in target_dtype or 'date' in target_dtype: return pd.to_datetime(series, errors='coerce')
            elif 'timedelta' in target_dtype: return pd.to_timedelta(series, errors='coerce')
            elif 'int' in target_dtype.lower(): return pd.to_numeric(series, errors='coerce').astype(target_dtype)
            elif 'float' in target_dtype.lower(): return pd.to_numeric(series, errors='coerce').astype(target_dtype)
            elif 'bool' in target_dtype.lower(): return series.astype('bool', errors='ignore')
            elif 'str' in target_dtype.lower() or 'object' in target_dtype: return series.astype(str)
            else: return series.astype(target_dtype, errors='ignore')
        except Exception as e:
            print(f"Warning: Explicit conversion to {target_dtype} failed with error: {e}")
            return series
    
    if not isinstance(df, pd.DataFrame): raise ValueError("Input must be a pandas DataFrame")
    if len(df) == 0: 
        print("Warning: DataFrame is empty")
        return df
    
    df_converted = df.copy()
    dtype = dtype or {}
    
    for col in df_converted.columns:
        if col in dtype:
            print(f"Column '{col}': Explicit conversion to {dtype[col]}...")
            df_converted[col] = convert_explicit_dtype(df_converted[col], dtype[col])
            continue
        
        if df_converted[col].dtype == 'object' or pd.api.types.is_string_dtype(df_converted[col]):
            filtered = df_converted[col].dropna()
            filtered = filtered[filtered.astype(str).str.strip() != '']
            if len(filtered) == 0: continue
            
            filtered_sorted = filtered.sort_values().reset_index(drop=True)
            segment_size = max(1, int(len(filtered_sorted) * 0.05))
            segment_1, segment_2 = filtered_sorted.head(segment_size).tolist(), filtered_sorted.tail(segment_size).tolist()
            result_1, result_2 = identify_datatype(segment_1), identify_datatype(segment_2)
            
            if result_1 == result_2 and result_1 != 'string':
                print(f"Column '{col}': Detected as {result_1}, converting...")
                df_converted[col] = convert_column(df_converted[col], result_1)
            elif result_1 != result_2:
                print(f"Column '{col}': Mismatch detected (segment_1: {result_1}, segment_2: {result_2}), skipping conversion")
    return df_converted

def detect_outliers(df, columns=None, method='iqr', threshold=1.5, z_thresh=3.0, drop_nans=True):
    
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame.")
    
    dfc = df.copy()
    if columns is None:
        columns = dfc.select_dtypes(include=[np.number]).columns.tolist()
    
    if not columns:
        return pd.Series(True, index=dfc.index)  # No numeric columns, all good
    
    mask = pd.Series(True, index=dfc.index)
    
    for col in columns:
        x = dfc[col].dropna() if drop_nans else dfc[col]  # Drop NaNs for calc if specified
        if len(x) == 0:  # All NaN, treat as non-outlier or skip
            continue
        
        if method == 'iqr':
            q1, q3 = x.quantile([0.25, 0.75])
            iqr = q3 - q1
            if iqr == 0:  # No variation, skip
                continue
            lower, upper = q1 - threshold * iqr, q3 + threshold * iqr
            col_mask = dfc[col].between(lower, upper, inclusive='both')
            if not drop_nans:
                col_mask = col_mask.fillna(False)  # Treat NaNs as outliers
            mask &= col_mask
        elif method == 'zscore':
            z = (x - x.mean()) / x.std(ddof=1)  # Sample std
            col_mask = pd.Series(abs(z) < z_thresh, index=x.index)
            if drop_nans:
                full_mask = pd.Series(True, index=dfc.index)
                full_mask.loc[x.index] = col_mask
                col_mask = full_mask
            else:
                col_mask = col_mask.reindex(dfc.index).fillna(False)  # NaNs as outliers
            mask &= col_mask
        else:
            raise ValueError("Method must be 'iqr' or 'zscore'.")
    
    return mask


def replace_outliers_with_zero_safe(df, columns=None, method='iqr', threshold=1.5, z_thresh=3.0, 
                                    drop_nans=True, replace_nans_with_zero=True):
    """
    Replace outliers in numeric columns (including object-type numeric strings) with 0.
    Non-numeric strings are left as-is. NaNs can be optionally replaced with 0.
    >>> df_clean = replace_outliers_with_zero_safe(df, method='iqr')
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame.")
    dfc = df.copy()
    if columns is None:
        numeric_cols = dfc.select_dtypes(include=[np.number]).columns.tolist()
        object_cols = []
        for col in dfc.select_dtypes(include=['object']).columns:
            if dfc[col].dtype == 'object':  # Only process if convertible
                try:
                    temp = pd.to_numeric(dfc[col], errors='coerce').notna().sum()
                    if temp > 0:  # At least one numeric value
                        object_cols.append(col)
                except:
                    pass
        columns = numeric_cols + object_cols
    
    if not columns:
        return dfc
    
    for col in columns:
        original = dfc[col].copy()  # Preserve originals for non-numeric
        x_numeric = pd.to_numeric(dfc[col], errors='coerce')
        non_numeric_mask = x_numeric.isna()  # Where original was non-numeric string
        x_for_calc = x_numeric.dropna() if drop_nans else x_numeric
        if len(x_for_calc) == 0:
            continue  # All non-numeric or NaN, leave as-is
        if method == 'iqr':
            q1, q3 = x_for_calc.quantile([0.25, 0.75])
            iqr = q3 - q1
            if iqr == 0:
                continue
            lower, upper = q1 - threshold * iqr, q3 + threshold * iqr
            outlier_mask = ~x_numeric.between(lower, upper, inclusive='both')
            if not drop_nans:
                outlier_mask = outlier_mask.fillna(True)  # Treat NaNs as outliers
        elif method == 'zscore':
            z = (x_for_calc - x_for_calc.mean()) / x_for_calc.std(ddof=1)
            outlier_mask = pd.Series(abs(z) >= z_thresh, index=x_for_calc.index)
            if drop_nans:
                full_outlier = pd.Series(False, index=dfc.index)
                full_outlier.loc[x_for_calc.index] = outlier_mask
                outlier_mask = full_outlier
            else:
                outlier_mask = outlier_mask.reindex(dfc.index).fillna(True)  # NaNs as outliers
        else:
            raise ValueError("Method must be 'iqr' or 'zscore'.")
        
        to_replace = outlier_mask & ~non_numeric_mask  # Only numeric outliers
        dfc.loc[to_replace, col] = 0
        
        if replace_nans_with_zero:
            nan_mask = x_numeric.isna() & dfc[col].isna()  # True NaNs
            dfc.loc[nan_mask, col] = 0
    
    return dfc

def _missing_pct(s: pd.Series) -> float:
    total = len(s)
    return 0.0 if total == 0 else s.isna().sum() * 100 / total


def try_datetime(series: pd.Series, threshold: float = 5) -> Tuple[pd.Series, bool]:
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return series, False
    cleaned = series.replace(r'^\s*$', np.nan, regex=True)
    pre_pct = _missing_pct(cleaned)
    dt = pd.to_datetime(cleaned, errors='coerce')
    post_pct = _missing_pct(dt)
    if post_pct - pre_pct < threshold:
        return dt, True
    return series, False


def try_numeric(series: pd.Series, threshold: float = 5) -> Tuple[pd.Series, bool]:
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return series, False
    cleaned = series.replace(r'^\s*$', np.nan, regex=True)
    pre_pct = _missing_pct(cleaned)
    num = pd.to_numeric(cleaned, errors='coerce')
    post_pct = _missing_pct(num)
    if post_pct - pre_pct < threshold:
        return num, True
    return series, False


def try_boolean(series: pd.Series, threshold: float = 5) -> Tuple[pd.Series, bool]:
    if pd.api.types.is_bool_dtype(series):
        return series, False
    cleaned = series.replace(r'^\s*$', np.nan, regex=True).astype(str).str.strip().str.lower()
    mapping = {'true': True, 'false': False, 'yes': True, 'no': False,
               'y': True, 'n': False, 't': True, 'f': False, '1': True, '0': False}
    unique_vals = cleaned.dropna().unique()
    if any(val not in mapping for val in unique_vals):
        return series, False
    pre_pct = _missing_pct(cleaned)
    bools = cleaned.map(mapping).astype('boolean')
    post_pct = _missing_pct(bools)
    if post_pct - pre_pct < threshold:
        return bools, True
    return series, False


def smart_convert(df: pd.DataFrame, threshold: float = 5) -> Tuple[pd.DataFrame, Dict[str, str]]:
    out = df.copy()
    info = {}
    for col in out.columns:
        s, ok = try_datetime(out[col], threshold)
        if ok:
            out[col] = s
            info[col] = 'datetime'
            continue
        s, ok = try_numeric(out[col], threshold)
        if ok:
            out[col] = s
            info[col] = 'numeric'
            continue
        s, ok = try_boolean(out[col], threshold)
        if ok:
            out[col] = s
            info[col] = 'boolean'
    return out, info


def get_sample(series, ratio=0.05, min_s=5):
    clean = series.dropna()
    clean = clean[clean.astype(str).str.strip() != '']
    if clean.empty: return []
    n, seg = len(clean), max(min_s, int(len(clean) * ratio))
    return clean.astype(str).tolist() if n <= 2 * seg else clean.head(seg).astype(str).tolist() + clean.tail(seg).astype(str).tolist()


def auto_cast(df: pd.DataFrame, dtype: Optional[Dict] = None, threshold: float = 5, use_patterns: bool = True, verbose: bool = False) -> Tuple[pd.DataFrame, Dict[str, str]]:
    result, info = smart_convert(df, threshold)
    if use_patterns:
        result = cast_df(result, dtype=dtype)
    return result, info

if __name__ == '__main__':
    df = pd.DataFrame({
        'date_col': ['2023-01-01', '2023-01-02', '2023-01-03'],
        'num_col': ['100', '200', '300'],
        'bool_col': ['true', 'false', 'yes'],
        'text_col': ['abc', 'def', 'ghi']
    })

    df_converted, conversion_info = smart_convert(df, threshold=5)
    print(df, df_converted, conversion_info)
    print(df_converted.dtypes)
