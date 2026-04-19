"""
Oracle Metadata Freshness Monitor Core Pipeline.
Reads heavily from Oracle's internal Data Dictionary to aggressively reduce 
thousands of tables into a manageable active subset, safely extracts metadata, 
and probes their latencies.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, List

import pandas as pd
from sqlalchemy.engine import Engine
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

def get_active_tables(engine: Engine, schema: str, lookback_days: int = 7) -> pd.DataFrame:
    """Identify highly active tables over the last N days by reading the Data Dictionary."""
    query = text(f"""
        SELECT table_name, inserts, updates, deletes, timestamp as last_dml
        FROM all_tab_modifications
        WHERE table_owner = :owner
          AND timestamp > SYSDATE - :days
          AND (inserts + updates) > 0
    """)
    try:
        return pd.read_sql(query, engine, params={"owner": schema.upper(), "days": lookback_days})
    except SQLAlchemyError as exc:
        logger.error("Failed executing get_active_tables: %s", exc)
        return pd.DataFrame()

def find_candidate_time_cols(engine: Engine, schema: str) -> pd.DataFrame:
    """Seek timestamp columns indicating fresh telemetry in tables."""
    query = text("""
        SELECT table_name, column_name, data_type
        FROM all_tab_columns
        WHERE owner = :owner
          AND data_type IN ('DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP(6)')
          AND (
               UPPER(column_name) LIKE '%TIME%'
            OR UPPER(column_name) LIKE '%DATE%'
            OR UPPER(column_name) LIKE '%PERIOD%'
            OR UPPER(column_name) LIKE '%LOAD%'
            OR UPPER(column_name) LIKE '%EVENT%'
          )
    """)
    try:
        return pd.read_sql(query, engine, params={"owner": schema.upper()})
    except SQLAlchemyError as exc:
        logger.error("Failed executing find_candidate_time_cols: %s", exc)
        return pd.DataFrame()

def get_latest_partition_value(engine: Engine, schema: str, table_name: str) -> Optional[str]:
    """Attempt 100x faster timestamp grab without scanning table rows!"""
    query = text("""
        SELECT high_value
        FROM all_tab_partitions
        WHERE table_owner = :owner
          AND table_name = :table_name
        ORDER BY partition_position DESC
        FETCH FIRST 1 ROW ONLY
    """)
    try:
        with engine.connect() as conn:
            result = conn.execute(query, {"owner": schema.upper(), "table_name": table_name.upper()}).scalar()
            return str(result) if result else None
    except SQLAlchemyError:
        return None

def probe_freshness(engine: Engine, schema: str, target_df: pd.DataFrame, throttle_secs: float = 2.0) -> pd.DataFrame:
    """Iteratively check freshness safely using throttles."""
    results = []
    logger.info("Probing freshness for %d tables...", len(target_df))
    
    for i, row in target_df.iterrows():
        table_name = row['table_name']
        col_name = row['column_name']
        
        # 1. Attempt best practice: Partition metadata read
        latest_time = get_latest_partition_value(engine, schema, table_name)
        
        # 2. Fallback to SQL Agnostic MAX() aggregation query
        if not latest_time:
            query = text(f"SELECT MAX({col_name}) FROM {schema}.{table_name}")
            try:
                with engine.connect() as conn:
                    latest_time = str(conn.execute(query).scalar())
            except Exception as e:
                latest_time = None
                logger.error("Failed probing table %s: %s", table_name, e)
                
        results.append({
            "owner": schema.upper(),
            "table_name": table_name,
            "time_column": col_name,
            "last_update": latest_time
        })
        
        # Protective sleep throttle 
        if i < len(target_df) - 1:
            time.sleep(throttle_secs)
        
    return pd.DataFrame(results)


def run_oracle_audit(engine: Engine, schema: str, lookback_days: int = 7, throttle_secs: float = 2.0) -> pd.DataFrame:
    """Primary Public Facade API for running the Telecom Oracle Monitor"""
    
    if "oracle" not in engine.dialect.name.lower():
        logger.error("oracle_monitor.py is strictly tuned for Oracle. Engine passed: %s.", engine.dialect.name)
        raise ValueError("run_oracle_audit explicitly requires an Oracle SQLAlchemy Engine.")
        
    logger.info("Commencing Oracle Audit on schema '%s' (Lookback: %d days).", schema, lookback_days)
    
    # 1. Find the needles in the haystack safely
    recent_tables = get_active_tables(engine, schema, lookback_days)
    if recent_tables.empty:
        logger.warning("No active modifications detected in the last %d days.", lookback_days)
        return pd.DataFrame()
        
    time_cols = find_candidate_time_cols(engine, schema)
    if time_cols.empty:
        logger.warning("No tracking timestamp columns could be detected.")
        return pd.DataFrame()

    # 2. Mash the DataFrames together
    targets = pd.merge(recent_tables, time_cols, on="table_name", how="inner")
    
    if targets.empty:
        logger.warning("Intersection between active tables and time_column tables returned empty.")
        return pd.DataFrame()
        
    logger.info("Found %d intersecting target tables to probe.", len(targets))
    
    # 3. Probe the databases
    return probe_freshness(engine, schema, targets, throttle_secs)
