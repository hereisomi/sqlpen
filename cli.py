"""
SqlSql CLI — Command-Line Interface for zero-config ETL execution.
"""
from __future__ import annotations

import logging
import sys
import os
import json
from typing import Any

import click
import pandas as pd
import sqlalchemy as sa
from df_tosql import df_tosql

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("sqlsql")

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

def _read_data(source: str) -> pd.DataFrame:
    """Read data source into a pandas DataFrame."""
    lower = source.lower()
    if lower.endswith(".parquet"):
        return pd.read_parquet(source)
    if lower.endswith(".json"):
        return pd.read_json(source)
    if lower.endswith((".xls", ".xlsx")):
        return pd.read_excel(source)
    if lower.endswith(".csv") or lower.startswith("http://") or lower.startswith("https://"):
        return pd.read_csv(source)
    raise ValueError(f"Unsupported data source extension: {source}")

def _get_engine(url: str | None) -> sa.engine.Engine:
    if url:
        return sa.create_engine(url)
    
    # Check .env and environment variables
    env_vars = ["DATABASE_URL", "SQLALCHEMY_URL", "DB_URL"]
    
    # Try .env file first
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() in env_vars:
                        return sa.create_engine(v.strip().strip("'\""))
                        
    # Try environment variables
    for var in env_vars:
        url = os.getenv(var)
        if url:
            return sa.create_engine(url)
            
    raise ValueError("No database URL provided via --url or .env (DATABASE_URL)")

@click.group()
@click.version_option(version="0.1.0", prog_name="sqlsql")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx, verbose):
    """SqlSql -- Modernized ETL & DML Pipeline CLI."""
    ctx.ensure_object(dict)
    _setup_logging(verbose)

@cli.command()
@click.argument("source")
@click.option("--table", required=True, help="Target table name")
@click.option("--url", help="Database URL")
@click.option("--ops", default="insert", type=click.Choice(["insert", "upsert", "update", "update_track"]), help="Operation mode")
@click.option("--pk", help="Comma-separated primary key columns (required for upsert)")
@click.option("--apply-ddl", is_flag=True, help="Apply detected DDL changes")
@click.option("--chunk", default=10000, type=int, help="Chunk size for bulk operations")
@click.option("--trace-sql", is_flag=True, help="Enable SQL tracing")
def load(source, table, url, ops, pk, apply_ddl, chunk, trace_sql):
    """Load data from a file into the database."""
    try:
        engine = _get_engine(url)
        df = _read_data(source)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    kwargs = {
        "engine": engine,
        "df": df,
        "table": table,
        "if_exist": ops,
        "add_new_column": apply_ddl,
        "chunk": chunk,
    }

    if ops == "upsert":
        if not pk:
            click.echo("Error: --pk is required for upsert mode", err=True)
            sys.exit(1)
        kwargs["table_constraints"] = {"pk": [k.strip() for k in pk.split(",")]}

    try:
        click.echo(f"Running {ops} for {source} -> {table}...")
        result = df_tosql(**kwargs)
        click.echo(f"Success! Result: {json.dumps(result, indent=2)}")
    except Exception as e:
        click.echo(f"Pipeline failed: {e}", err=True)
        sys.exit(1)

@cli.command()
def setup():
    """Launch the interactive setup wizard."""
    from utils.wizard import setup_database_wizard
    setup_database_wizard()

@cli.command()
def interactive():
    """Launch the full interactive menu."""
    from utils.wizard import interactive_menu
    interactive_menu()

@cli.command()
@click.argument("source")
@click.option("--table", required=True, help="Target table name")
@click.option("--pk", required=True, help="Primary key column(s) for harness")
@click.option("--constraint", help="Constraint column(s) for UPSERT/UPDATE (defaults to --pk)")
@click.option("--url", help="Database URL")
@click.option("--chunk", default=10000, type=int, help="Chunk size")
@click.option("--clean", is_flag=True, default=True, help="Sanitize column names")
@click.option("--cast", is_flag=True, default=True, help="Auto-cast types")
@click.option("--outlier", default=0.5, type=float, help="IQR outlier threshold (0=disabled)")
@click.option("--auto-profiling", is_flag=True, default=False, help="Auto-infer PK from data")
def test(source, table, pk, constraint, url, chunk, clean, cast, outlier, auto_profiling):
    """Run full INSERT -> UPSERT -> UPDATE harness for a CSV file."""
    try:
        engine = _get_engine(url)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    from pipeline_runner import run_csv_pipeline
    try:
        report = run_csv_pipeline(
            csv_path=source,
            engine=engine,
            table=table,
            pk_cols=pk,
            constraint_cols=constraint or pk,
            chunk=chunk,
            validate=True,
            clean=clean,
            cast=cast,
            outlier=outlier,
            auto_profiling=auto_profiling,
        )
        click.echo(report.summary())
    except Exception as e:
        click.echo(f"Harness failed: {e}", err=True)
        sys.exit(1)

@cli.command()
@click.option("--config", "config_path", default="config.yml", type=click.Path(), help="Path to config.yml")
def run(config_path):
    """Execute batch jobs from config.yml."""
    from utils.batch import execute_batch
    execute_batch(config_path)

def main():
    if len(sys.argv) == 1:
        # Launch interactive menu if no arguments provided
        from utils.wizard import interactive_menu
        interactive_menu()
    else:
        cli()

if __name__ == "__main__":
    main()
