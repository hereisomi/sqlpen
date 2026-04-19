"""
csvdog: Automated Incremental Directory Watchdog.
Loops over directories, orchestrates delta-tracking using a JSON manifest,
and securely invokes df_tosql avoiding redundant database injections.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import os

import pandas as pd
from sqlalchemy.engine import Engine

from df_tosql import df_tosql
from utils.cleaner import quick_clean
from utils.profiler import profile_dataframe, get_pk

logger = logging.getLogger(__name__)


def csvdog(
    filepath: str,
    engine: Engine,
    schema: str = 'mycsv.json',
    chunk: int = 10000,
    outlier: float = 0.5
) -> Dict[str, Any]:
    """
    Scans a directory for CSVs and asynchronously validates their status against
    a local JSON tracking schema.
    
    If modified, it automatically infers PKs and UPSERTs into the engine.
    If unaltered (even following a rigorous failure), it skips securely.
    """
    directory = Path(filepath)
    if not directory.exists() or not directory.is_dir():
        raise ValueError(f"Directory not found: {directory}")

    schema_file = Path(schema)
    tracker: Dict[str, Any] = {}
    
    if schema_file.exists():
        try:
            tracker = json.loads(schema_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Failed to decode %s. Building generic empty state map.", schema_file.name)
            tracker = {}

    results = {}

    for csv_path in directory.glob("*.csv"):
        table_name = csv_path.stem.lower().replace(" ", "_")
        current_mtime = csv_path.stat().st_mtime
        state = tracker.get(table_name, {})

        # --- Skip Rule Engine ---
        # Did the modification timestamp change?
        if state and state.get("mtime") == current_mtime:
            if state.get("status") == "failed":
                logger.info("[SKIP] '%s' heavily failed previously and is unaltered.", table_name)
            else:
                logger.info("[SKIP] '%s' is unaltered.", table_name)
            results[table_name] = "skipped"
            continue

        try:
            logger.info("[RUN] Initiating parsing profile for '%s'...", table_name)
            df = pd.read_csv(csv_path)
            
            # Prevent injection bugs inside SQL mapping layer
            df = quick_clean(df)
            
            pk_cols = state.get("pk")
            if_exist = "upsert"
            
            # Profiling Engine Fallback
            if not pk_cols:
                logger.info("[RUN] Auto-profiling '%s' for primary key deduction...", table_name)
                info = profile_dataframe(df)
                _, _, meta = get_pk(df, info)
                pk_cols = meta.get("components", [])
                if not pk_cols:
                    if not df.empty:
                        pk_cols = [df.columns[0]] # Hard fallback for arbitrary flat files
                        logger.warning("[RUN] Profiler boundary fallback induced. Forcing PK=%s", pk_cols)
                    else:
                        raise ValueError("Dataframe completely empty. Aborting PK mapping.")
                        
                if_exist = "replace"  # New table initialization

            # Invoke Pipeline Facade
            crud_result = df_tosql(
                df=df,
                table=table_name,
                engine=engine,
                if_exist=if_exist,
                chunk=chunk,
                table_constraints={"pk": pk_cols} if pk_cols else None,
                clean=False,   # Done locally
                cast=True,
                auto_profiling=False, # Handled locally prior
                outlier=outlier
            )
            
            tracker[table_name] = {
                "mtime": current_mtime,
                "pk": pk_cols,
                "status": "success",
                "file": str(csv_path),
                "rows": len(df)
            }
            results[table_name] = "success"

        except Exception as e:
            logger.error("[FAIL] Target '%s' aborted: %s", table_name, str(e))
            tracker[table_name] = {
                "mtime": current_mtime,
                "pk": state.get("pk"),  # Preserve historical PK mappings if failure hits parsing algorithm
                "status": "failed",
                "file": str(csv_path),
                "error": str(e)
            }
            results[table_name] = "failed"
            
        # Serialize intermediate bounds immediately to save metrics on abrupt crashes
        from utils.logger import SEP
        schema_file.write_text(json.dumps(tracker, indent=4), encoding="utf-8")
        
    return results
