"""
casting.py - Enhanced DataFrame Type Casting Engine

High-performance, intelligent type inference with caching, validation, and parallel processing.
Combines rule-based patterns, ML-like classification, and statistical validation.
"""

from __future__ import annotations
import json, logging, math, re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

_LOG = logging.getLogger(__name__)

@dataclass
class CastConfig:
    use_transform: bool = True
    use_ml: bool = False
    infer_threshold: float = 0.9
    nan_threshold: float = 0.30
    max_sample_size: int = 1000
    parallel: bool = False
    validate_conversions: bool = True
    max_null_increase: float = 0.1
    chunk_size: int = 50000
    return_dtype_meta: bool = False  # For ddl_cl_v2 compatibility

_PATTERNS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$"), "%Y-%m-%d %H:%M"),
    (re.compile(r"\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y"),
)

_PATTERN_DICT = {
    "timestamp": [r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}"],
    "date": [r"\d{4}-\d{2}-\d{2}$", r"\d{2}/\d{2}/\d{4}$"],
    "boolean": [r"(true|false)$", r"(yes|no)$", r"(y|n)$"],
    "integer": [r"-?\d+$"],
    "float": [r"-?\d*\.?\d+$", r"-?\d*\.?\d*[eE][+-]?\d+$"],
}

_BOOL_MAP = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False, "t": True, "f": False, "1": True, "0": False, "on": True, "off": False}
_CURRENCY_PATTERN = re.compile(r'[\$â‚¬Â£Â¥â‚¹,\s]')
_INT_PATTERN = re.compile(r'^-?\d+$')
_FLOAT_PATTERN = re.compile(r'^-?\d*\.?\d+([eE][-+]?\d+)?$')

@lru_cache(maxsize=10000)
def _cached_infer_one(v: str) -> str:
    for name, pats in _PATTERN_DICT.items():
        if any(re.fullmatch(p, v, re.IGNORECASE) for p in pats):
            return name
    return "string"

def _smart_sample(series: pd.Series, max_size: int) -> pd.Series:
    clean = series.dropna()
    if len(clean) <= max_size:
        return clean
    n_chunks = min(5, len(clean) // 100)
    if n_chunks <= 1:
        return clean.sample(max_size, random_state=42)
    chunk_size = len(clean) // n_chunks
    samples = [clean.iloc[i*chunk_size:(i+1)*chunk_size].sample(min(max_size//n_chunks, chunk_size), random_state=42) for i in range(n_chunks)]
    return pd.concat(samples)

def _infer_with_confidence(sample: Sequence[str], threshold: float) -> tuple[str, float]:
    if not sample:
        return "string", 0.0
    types = [_cached_infer_one(str(v).strip()) for v in sample]
    counter = Counter(types)
    most_common = counter.most_common(1)[0]
    confidence = most_common[1] / len(sample)
    return most_common[0] if confidence >= threshold else "string", confidence

def _validate_conversion(original: pd.Series, converted: pd.Series, max_null_increase: float) -> bool:
    if len(original) == 0:
        return True
    null_before = original.isna().sum()
    null_after = converted.isna().sum()
    null_increase = (null_after - null_before) / len(original)
    return null_increase <= max_null_increase

def _convert_series_safe(series: pd.Series, target: str, config: CastConfig) -> pd.Series:
    try:
        if target == "timestamp":
            converted = pd.to_datetime(series, errors="coerce")
        elif target == "date":
            converted = pd.to_datetime(series, errors="coerce").dt.date
        elif target == "boolean":
            converted = series.astype(str).str.lower().str.strip().map(_BOOL_MAP)
        elif target == "integer":
            converted = pd.to_numeric(series, errors="coerce").astype("Int64")
        elif target == "float":
            converted = pd.to_numeric(series, errors="coerce").astype("Float64")
        else:
            return series
        if config.validate_conversions and not _validate_conversion(series, converted, config.max_null_increase):
            _LOG.warning(f"Conversion to {target} rejected due to high null increase")
            return series
        return converted
    except Exception as e:
        _LOG.debug(f"Conversion to {target} failed: {e}")
        return series

def _is_numeric_column(series: pd.Series, threshold: float = 0.9) -> bool:
    sample = series.dropna().astype(str).head(100)
    if len(sample) == 0:
        return False
    cleaned = sample.str.replace(_CURRENCY_PATTERN, '', regex=True).str.strip()
    return (cleaned.str.match(_INT_PATTERN) | cleaned.str.match(_FLOAT_PATTERN)).mean() > threshold

def _is_boolean_column(series: pd.Series, threshold: float = 0.9) -> bool:
    sample = series.dropna().astype(str).str.lower().str.strip().head(100)
    if len(sample) == 0:
        return False
    mapped = sample.map(_BOOL_MAP)
    return mapped.notna().mean() >= threshold

def _is_json_column(series: pd.Series, threshold: float = 0.9) -> bool:
    sample = series.dropna().head(100)
    if len(sample) == 0:
        return False
    def is_json(x):
        if isinstance(x, (dict, list)):
            return True
        if isinstance(x, str):
            x = x.strip()
            if (x.startswith('{') and x.endswith('}')) or (x.startswith('[') and x.endswith(']')):
                try:
                    json.loads(x)
                    return True
                except:
                    return False
        return False
    return sample.apply(is_json).mean() >= threshold

def _process_column(col_data: tuple) -> tuple[str, pd.Series]:
    col, series, dtype_override, config = col_data
    if dtype_override:
        try:
            return col, pd.to_numeric(series, errors='coerce').astype(dtype_override) if 'int' in dtype_override or 'float' in dtype_override else series.astype(dtype_override)
        except:
            pass
    if not (series.dtype == "object" or pd.api.types.is_string_dtype(series)):
        return col, series
    sample = _smart_sample(series.astype(str).str.strip(), config.max_sample_size)
    sample = sample[sample != ""]
    if sample.empty:
        return col, series
    inferred_type, confidence = _infer_with_confidence(sample.tolist(), config.infer_threshold)
    if inferred_type != "string" and confidence > 0.7:
        return col, _convert_series_safe(series, inferred_type, config)
    num_coerced = pd.to_numeric(series, errors='coerce')
    if num_coerced.notna().mean() >= config.infer_threshold:
        return col, num_coerced
    dt_coerced = pd.to_datetime(series, errors='coerce')
    if dt_coerced.notna().mean() >= config.infer_threshold:
        return col, dt_coerced
    return col, series

def _transform_dtypes_enhanced(df: pd.DataFrame, dtype_map: Mapping[str, str], config: CastConfig) -> pd.DataFrame:
    result = df.copy()
    dtype_map = dtype_map or {}

    # Enforce explicit dtype map even when use_transform is enabled (bugfix: do not skip caller overrides)
    for col, target in dtype_map.items():
        if col not in result.columns:
            continue
        try:
            if 'int' in str(target).lower() or 'float' in str(target).lower():
                converted = pd.to_numeric(result[col], errors='coerce').astype(target)
            else:
                converted = result[col].astype(target)
            result[col] = converted
        except (TypeError, ValueError) as e:
            _LOG.debug(f"Explicit dtype cast for column '{col}' to '{target}' failed: {e}")

    for col in result.select_dtypes(include=['object']).columns:
        if col in dtype_map:
            # Skip inference for explicitly mapped columns to preserve requested dtype
            continue
        if any(x in col.lower() for x in ['date', 'time', 'timestamp', '_at', 'created', 'updated']):
            if not _is_json_column(result[col]):
                converted = pd.to_datetime(result[col], errors="coerce")
                if _validate_conversion(result[col], converted, config.max_null_increase):
                    result[col] = converted
        elif _is_numeric_column(result[col]):
            cleaned = result[col].astype(str).str.replace(_CURRENCY_PATTERN, '', regex=True).str.strip()
            converted = pd.to_numeric(cleaned, errors='coerce')
            if _validate_conversion(result[col], converted, config.max_null_increase):
                result[col] = converted
        elif _is_boolean_column(result[col]):
            result[col] = result[col].astype(str).str.lower().str.strip().map(_BOOL_MAP)
    return result

def _to_dataframe(obj: Any) -> pd.DataFrame:
    if isinstance(obj, pd.DataFrame):
        return obj.copy()
    if isinstance(obj, dict):
        try:
            return pd.DataFrame(obj)
        except ValueError:
            return pd.DataFrame([obj])
    if isinstance(obj, (list, tuple)) and obj and isinstance(obj[0], dict):
        return pd.DataFrame(obj)
    raise ValueError(f"Cannot convert {type(obj)} to DataFrame")

def cast_df(obj: Any, dtype: Mapping[str, str] | None = None, config: CastConfig | None = None, **kwargs) -> pd.DataFrame:
    """Enhanced DataFrame casting with performance optimizations and validation."""
    config = config or CastConfig(**kwargs)
    try:
        df = _to_dataframe(obj)
    except Exception as e:
        _LOG.error(f"Failed to convert input: {e}")
        raise ValueError(f"Invalid input type: {type(obj)}") from e
    if df.empty:
        return df
    if df.shape[0] > config.chunk_size:
        return _cast_df_chunked(df, dtype, config)
    if config.use_transform:
        return _transform_dtypes_enhanced(df, dtype or {}, config)
    dtype = dtype or {}
    if config.parallel and len(df.columns) > 4:
        col_data = [(col, df[col], dtype.get(col), config) for col in df.columns]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(_process_column, col_data))
        return pd.DataFrame({col: series for col, series in results})
    out = df.copy()
    for col in df.columns:
        col_result = _process_column((col, df[col], dtype.get(col), config))
        out[col] = col_result[1]
    return out

def _cast_df_chunked(df: pd.DataFrame, dtype: Mapping[str, str] | None, config: CastConfig) -> pd.DataFrame:
    chunks = []
    for i in range(0, len(df), config.chunk_size):
        chunk = df.iloc[i:i+config.chunk_size]
        if config.use_transform:
            processed = _transform_dtypes_enhanced(chunk, dtype or {}, config)
        else:
            processed = cast_df(chunk, dtype, config)
        chunks.append(processed)
    return pd.concat(chunks, ignore_index=True)

def auto_cast(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Quick auto-casting with sensible defaults."""
    return cast_df(df, config=CastConfig(use_transform=True, validate_conversions=True, **kwargs))

def fast_cast(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Fast casting with minimal validation."""
    return cast_df(df, config=CastConfig(validate_conversions=False, max_sample_size=100, **kwargs))

def safe_cast(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Conservative casting with strict validation."""
    return cast_df(df, config=CastConfig(max_null_increase=0.05, infer_threshold=0.95, **kwargs))
