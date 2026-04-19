"""
Batch execution engine for SqlSql.
Parses a YAML configuration file and executes multiple ETL jobs.
"""
import yaml
import logging
import os
import pandas as pd
import sqlalchemy as sa
from typing import List, Dict, Any
from df_tosql import df_tosql

logger = logging.getLogger("sqlsql.batch")

def execute_batch(config_path: str):
    """Load config.yml and run all defined jobs."""
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error parsing YAML: {e}")
        return

    jobs = config.get("jobs", [])
    if not jobs:
        print("No jobs defined in config.")
        return

    default_url = config.get("database_url") or os.environ.get("DATABASE_URL")
    
    print(f"Starting batch execution of {len(jobs)} jobs...")
    
    success_count = 0
    for i, job in enumerate(jobs, 1):
        source = job.get("source")
        table = job.get("table")
        if not source or not table:
            print(f"  [SKIP] Job {i}: Missing source or table")
            continue

        url = job.get("url", default_url)
        if not url:
            print(f"  [SKIP] Job {i}: No database URL provided")
            continue

        mode = job.get("mode", "insert")
        pk = job.get("pk")
        
        print(f"  [{i}/{len(jobs)}] {source} -> {table} ({mode})...")
        
        try:
            # Resolve relative paths
            if not source.startswith(("http://", "https://")) and not os.path.isabs(source):
                source = os.path.join(os.path.dirname(os.path.abspath(config_path)), source)

            # Read data
            if source.endswith(".csv"): df = pd.read_csv(source)
            elif source.endswith(".parquet"): df = pd.read_parquet(source)
            elif source.endswith(".json"): df = pd.read_json(source)
            elif source.endswith((".xls", ".xlsx")): df = pd.read_excel(source)
            else:
                print(f"    [FAIL] Unsupported extension for {source}")
                continue

            engine = sa.create_engine(url)
            kwargs = {
                "engine": engine,
                "df": df,
                "table": table,
                "if_exist": mode,
                "add_new_column": job.get("apply_ddl", False),
                "chunk": job.get("chunk", 10000),
            }
            if pk:
                kwargs["table_constraints"] = {"pk": [k.strip() for k in str(pk).split(",")]}

            df_tosql(**kwargs)
            print(f"    [OK] Success")
            success_count += 1
        except Exception as e:
            print(f"    [FAIL] {e}")

    print(f"\nBatch completed: {success_count}/{len(jobs)} jobs successful.")
