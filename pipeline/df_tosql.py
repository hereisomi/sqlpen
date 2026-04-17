"""
Unified facade for ETL workflow execution.

Provides `df_tosql` which orchestrates cleaning, casting, outlier quarantine,
profiling, and targeted CRUD execution automatically.

Supports multi-format extraction: DataFrame, CSV, Parquet, JSON, Excel,
remote URLs (http/https), and S3 paths.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from crud import auto_insert, auto_update, auto_upsert, CrudConfig, CrudResult
from config import ensure_table

logger = logging.getLogger(__name__)

# Optional tqdm import — gracefully degrade to no-op if missing
try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False
    def tqdm(iterable=None, *args, **kwargs):  # noqa: E302
        return iterable


# ---------------------------------------------------------------------------
# Multi-format source loader
# ---------------------------------------------------------------------------
_CHUNK_THRESHOLD = 200 * 1024 * 1024  # 200 MB


def _load_source(
    source: Union[pd.DataFrame, str, Path],
    chunk: int = 10_000,
) -> pd.DataFrame:
    """Resolve *source* into a DataFrame.

    Accepts:
    - pd.DataFrame (passthrough)
    - Local file path (.csv, .parquet, .json, .xlsx, .xls)
    - Remote URL (http:// or https://)
    - S3 path (s3://)

    For files larger than 200 MB, reads in chunks to avoid OOM.
    """
    if isinstance(source, pd.DataFrame):
        return source

    src = str(source).strip()

    # Remote URLs — pandas handles http/https/s3 natively
    if src.startswith(("http://", "https://", "s3://")):
        ext = Path(src.split("?")[0]).suffix.lower()
        logger.info("Loading remote source: %s (detected: %s)", src, ext or "csv")
        return _read_by_ext(src, ext or ".csv", chunk)

    # Local file
    path = Path(src)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")

    ext = path.suffix.lower()
    logger.info("Loading local source: %s (%s)", path.name, ext)

    # Check file size for chunked reading
    file_size = path.stat().st_size
    if file_size > _CHUNK_THRESHOLD and ext == ".csv":
        logger.info("Large file detected (%d MB). Using chunked reader.", file_size // (1024 * 1024))
        chunks = []
        reader = pd.read_csv(path, chunksize=chunk)
        for c in tqdm(reader, desc="Reading chunks") if _HAS_TQDM else reader:
            chunks.append(c)
        return pd.concat(chunks, ignore_index=True)

    return _read_by_ext(str(path), ext, chunk)


def _read_by_ext(path: str, ext: str, chunk: int) -> pd.DataFrame:
    """Read a file by extension."""
    if ext in (".csv", ".tsv", ".txt"):
        sep = "\t" if ext == ".tsv" else ","
        return pd.read_csv(path, sep=sep)
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if ext == ".json":
        return pd.read_json(path)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    # Default fallback: try CSV
    logger.warning("Unknown extension '%s', attempting CSV parse.", ext)
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Main pipeline facade
# ---------------------------------------------------------------------------
def df_tosql(
    df: Union[pd.DataFrame, str, Path],
    table: str,
    engine: Optional[Engine] = None,
    if_exist: str = 'insert',  # replace | update | insert | upsert
    schema: Optional[str] = None,
    chunk: int = 1000,
    constraint_cols: Union[List[str], str, None] = '',
    where: Optional[List[Any]] = None,
    expression: Optional[str] = None,
    add_new_column: bool = True,
    clean: bool = True,
    cast: bool = True,
    auto_profiling: bool = False,
    outlier: float = 0.5,
    schema_name: str = 'abc.json'
) -> CrudResult:
    """
    Robust pipeline facade to write a DataFrame (or file path) to a SQL database.
    Integrates cleaning, casting, outlier processing, and schema metadata dump.

    Parameters
    ----------
    df : DataFrame, str, or Path
        Source data. Accepts a DataFrame directly, or a path/URL to a
        CSV, Parquet, JSON, or Excel file. Remote URLs (http/s3) supported.
    table : str
        Target table name.
    engine : Engine, optional
        SQLAlchemy engine. If None, auto-resolved from DATABASE_URL env var.
    """
    # ── Resolve engine if not provided ──
    if engine is None:
        from config import get_engine_from_env
        engine = get_engine_from_env()

    # ── Resolve source into DataFrame ──
    data = _load_source(df, chunk)

    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError("Source must resolve to a non-empty DataFrame")

    if_exist = if_exist.strip().lower()
    if if_exist not in ("replace", "update", "insert", "upsert"):
        raise ValueError(f"Invalid if_exist option: {if_exist}")

    if if_exist == "update" and not where:
        raise ValueError("Cannot perform 'update' without 'where' conditions")

    data = data.copy()
    dialect = engine.dialect.name.lower()

    # 1. Clean / Sanitize Names
    if clean:
        logger.info("Pipeline: Sanitizing column names...")
        from utils.ddl import sanitize_dataframe_columns
        data, mapping = sanitize_dataframe_columns(data, server=dialect, allow_space=False, to_lower=True)
        def _remap(cols):
            if not cols: return cols
            return [mapping.get(str(c), str(c)) for c in cols]
    else:
        def _remap(cols): return cols

    # Parse constraint_cols
    pk_cols: List[str] = []
    if isinstance(constraint_cols, str):
        if constraint_cols.strip():
            pk_cols = [c.strip() for c in constraint_cols.split(",")]
    elif isinstance(constraint_cols, list):
        pk_cols = constraint_cols
    pk_cols = _remap(pk_cols)

    # 2. Type Casting
    if cast:
        logger.info("Pipeline: Auto-casting variable types...")
        from utils.trycast import auto_cast
        data, _ = auto_cast(data, use_patterns=True)

    # 3. Outlier Quarantine
    if outlier and outlier > 0:
        logger.info("Pipeline: Quarantining outliers (threshold=%s)...", outlier)
        from utils.trycast import replace_outliers_with_zero_safe
        data = replace_outliers_with_zero_safe(data, method='iqr', threshold=outlier)

    # 4. Auto Profiling & PK Inference
    if auto_profiling:
        logger.info("Pipeline: Profiling dataframe...")
        from utils.profiler import profile_dataframe, get_pk
        df_info = profile_dataframe(data)
        if not pk_cols and if_exist == 'upsert':
            _, pk_name, _ = get_pk(data, df_info)
            if pk_name:
                pk_cols = [pk_name]
                logger.info("Pipeline: Inferred Primary Key: %s", pk_cols)

    if if_exist == 'upsert' and not pk_cols:
        # Let crud layer auto-discover from existing table PK (zero-config)
        logger.info("Pipeline: No constraint provided — CRUD will auto-discover PK from table.")

    # 5. Schema JSON Dump
    if schema_name:
        logger.info("Pipeline: Dumping schema metadata to %s", schema_name)
        from utils.ddl import df_to_ddl_and_schema
        _, _, schema_dict = df_to_ddl_and_schema(
            data, table, dialect, schema_name=schema, pk=pk_cols, sanitize=False
        )
        json_path = Path(schema_name)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(schema_dict, f, indent=2)

    # 6. Table Lifecycle & Execution
    inspector = sa.inspect(engine)
    table_exists = inspector.has_table(table, schema=schema)

    if if_exist == 'update' and not table_exists:
        raise ValueError(f"Cannot perform 'update' because table '{table}' does not exist.")

    cfg = CrudConfig(chunk_size=chunk, add_missing_cols=add_new_column, strict=False)

    if if_exist == 'replace':
        logger.info("Pipeline: REPLACING table '%s'", table)
        ensure_table(engine, data, table, schema=schema, pk_cols=pk_cols, if_exists="drop")
        return auto_insert(engine, data, table, config=cfg)

    elif if_exist == 'update':
        logger.info("Pipeline: UPDATING table '%s'", table)
        return auto_update(engine, data, table, where=where, expression=expression, config=cfg)

    elif if_exist == 'upsert':
        logger.info("Pipeline: UPSERTING into table '%s'", table)
        if not table_exists:
            ensure_table(engine, data, table, schema=schema, pk_cols=pk_cols, if_exists="skip")
        return auto_upsert(engine, data, table, constrain=pk_cols or None, config=cfg)

    else:  # if_exist == 'insert'
        logger.info("Pipeline: INSERTING into table '%s'", table)
        if not table_exists:
            ensure_table(engine, data, table, schema=schema, pk_cols=pk_cols, if_exists="skip")
        return auto_insert(engine, data, table, config=cfg)
