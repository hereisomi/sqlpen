"""
Data normalization — convert any input to clean list[dict] or DataFrame.

Handles:
- dict / list[dict] / DataFrame input
- NaN / NaT / inf → None
- Pandas Timestamps → Python datetime
- NumPy scalars → Python natives
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Union

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

from .types import DataLike


def normalize_to_df(data: DataLike) -> "pd.DataFrame":
    """Convert any supported input to a pandas DataFrame."""
    if pd is None:
        raise RuntimeError("pandas is required for DataFrame operations")
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, Mapping):
        return pd.DataFrame([dict(data)])
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return pd.DataFrame(list(data))
    raise TypeError(f"Unsupported data type: {type(data).__name__}; expected dict, list[dict], or DataFrame")


def normalize_data(data: DataLike) -> List[Dict[str, Any]]:
    """Normalize input data into list[dict] with ultra-safe null handling.

    Converts:
     - pd.NaT → None
     - np.nan / np.inf → None
     - pd.Timestamp → datetime.datetime
     - np.integer/np.floating → Python int/float
    """
    if pd is not None and isinstance(data, pd.DataFrame):
        # Vectorized: replace NaN/NaT with None in one pass
        clean = data.astype(object).where(pd.notnull(data), None)
        records = clean.to_dict(orient="records")
    elif isinstance(data, Mapping):
        records = [dict(data)]
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        records = [dict(row) for row in data]
    else:
        raise TypeError(f"Unsupported data type: {type(data).__name__}")

    # Per-value cleanup for driver compatibility
    for rec in records:
        for key, val in rec.items():
            # Timestamp → pydatetime
            if hasattr(val, "to_pydatetime"):
                rec[key] = val.to_pydatetime()
                continue
            # pd.NaT
            if pd is not None and val is pd.NaT:
                rec[key] = None
                continue
            # None stays None
            if val is None:
                continue
            # np.nan / inf
            if np is not None and isinstance(val, (float, np.floating)):
                if np.isnan(val) or np.isinf(val):
                    rec[key] = None
                    continue
            # pd.isna catchall
            try:
                if pd is not None and pd.isna(val):
                    rec[key] = None
                    continue
            except (TypeError, ValueError):
                pass
            # np scalars → Python natives
            if np is not None:
                if isinstance(val, np.integer):
                    rec[key] = int(val)
                elif isinstance(val, np.floating):
                    rec[key] = float(val)
                elif isinstance(val, np.bool_):
                    rec[key] = bool(val)

    return records


def chunk_iter(rows: List[Dict[str, Any]], size: int):
    """Yield chunks of rows with given size."""
    for i in range(0, len(rows), size):
        yield rows[i : i + size]
