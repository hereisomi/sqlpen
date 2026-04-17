"""
SqlPen CLI — command-line interface for zero-config ETL execution.

Usage:
    sqlpen load data.csv --table users
    sqlpen test data.csv --table users --pk id --constraint email
    sqlpen run
    sqlpen run --config custom.yml
"""
from __future__ import annotations

import logging
import sys

import click

logger = logging.getLogger("sqlpen")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _get_default_table_name(source: str) -> str:
    """Derive a safe SQL table name from a file path or URL."""
    from pathlib import Path
    import re
    # If URL, split off query parameters
    path_part = source.split("?")[0]
    stem = Path(path_part).stem
    
    # Sanitize: lowercase, remove non-alphanumeric, don't start with digit
    s = stem.lower()
    s = re.sub(r"[^\w]", "_", s)
    s = re.sub(r"__+", "_", s).strip("_")
    if re.match(r"^\d", s):
        s = "_" + s
    return s or "table_1"


@click.group()
@click.version_option(version="0.1.0", prog_name="sqlpen")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx, verbose):
    """SqlPen -- Zero-Config ETL & DML Test Harness for Pandas → SQL."""
    ctx.ensure_object(dict)
    _setup_logging(verbose)


@cli.command()
@click.argument("source")
@click.option("--table", required=False, default=None, help="Target table name (inferred from source if omitted)")
@click.option("--url", default=None, help="Database URL (or set DATABASE_URL env)")
@click.option("--if-exist", "if_exist", default="insert",
              type=click.Choice(["insert", "replace", "upsert", "update"], case_sensitive=False),
              help="Table write mode")
@click.option("--schema", default=None, help="Database schema")
@click.option("--chunk", default=1000, type=int, help="Batch size for bulk operations")
@click.option("--constraint", default=None, help="Comma-separated unique constraint columns")
@click.option("--no-clean", is_flag=True, help="Skip column name sanitization")
@click.option("--no-cast", is_flag=True, help="Skip automatic type casting")
@click.option("--outlier", default=0.5, type=float, help="IQR outlier threshold (0=disabled)")
@click.option("--schema-name", default="", help="Path to dump schema JSON metadata")
def load(source, table, url, if_exist, constraint, schema, chunk, no_clean, no_cast, outlier, schema_name):
    """Load a file into a database table.

    SOURCE can be a local CSV/Parquet/JSON/Excel file path or a remote URL.
    """
    from config import get_engine_from_env
    from pipeline.df_tosql import df_tosql

    engine = get_engine_from_env(url)

    if not table:
        table = _get_default_table_name(source)
        click.echo(f"[INFO] No table specified. Inferred target table: '{table}'")

    result = df_tosql(
        df=source,
        table=table,
        engine=engine,
        if_exist=if_exist,
        schema=schema,
        chunk=chunk,
        constraint_cols=constraint or "",
        clean=not no_clean,
        cast=not no_cast,
        outlier=outlier,
        schema_name=schema_name,
    )
    click.echo(f"[OK] Done. success={result.success}  failed={result.failed}  method={result.method}")


@cli.command()
@click.argument("source")
@click.option("--table", required=False, default=None, help="Target table name (inferred from source if omitted)")
@click.option("--url", default=None, help="Database URL (or set DATABASE_URL env)")
@click.option("--pk", default=None, help="Comma-separated primary key columns")
@click.option("--constraint", default=None, help="Comma-separated constraint columns")
@click.option("--clean", is_flag=True, help="Sanitize column names")
@click.option("--cast", is_flag=True, help="Auto-cast variable types")
@click.option("--outlier", default=0.5, type=float, help="IQR outlier threshold (0=disabled)")
@click.option("--report-dir", default=None, type=click.Path(), help="Directory for harness report")
@click.option("--auto-profile", "auto_profile", is_flag=True, help="Auto-infer PK from data profile")
def test(source, table, url, pk, constraint, clean, cast, outlier, report_dir, auto_profile):
    """Run the csv_harness benchmark (INSERT -> UPSERT -> UPDATE) against a database.

    SOURCE must be a path to a CSV file.
    Generates a <csv_name>_harness.txt report with all SQL queries and diagnostics.
    """
    from pathlib import Path
    from config import get_engine_from_env
    from pipeline.csv_harness import run_csv_pipeline

    engine = get_engine_from_env(url)

    if not table:
        table = _get_default_table_name(source)
        click.echo(f"[INFO] No table specified. Inferred target table: '{table}'")

    report = run_csv_pipeline(
        csv_path=source,
        engine=engine,
        table=table,
        pk_cols=pk.split(",") if pk else "id",
        constraint_cols=constraint.split(",") if constraint else "id",
        clean=clean,
        cast=cast,
        outlier=outlier,
        report_dir=report_dir,
        auto_profiling=auto_profile,
    )
    click.echo(report.summary())

    # Tell the user where the report was written
    csv_stem = Path(source).stem
    if report_dir:
        rpath = Path(report_dir) / f"{csv_stem}_harness.txt"
    else:
        rpath = Path(source).parent / f"{csv_stem}_harness.txt"
    click.echo(f"\nReport written to: {rpath}")


@cli.command()
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to config.yml")
def run(config_path):
    """Execute all jobs defined in the config.yml 'jobs' section."""
    from pathlib import Path

    import yaml

    from config import get_engine_from_env
    from pipeline.df_tosql import df_tosql

    # Find config.yml
    resolved = Path(config_path) if config_path else None
    if resolved is None:
        for candidate in [Path.cwd() / "config.yml", Path(__file__).resolve().parent / "config.yml"]:
            if candidate.exists():
                resolved = candidate
                break

    if not resolved or not resolved.exists():
        click.echo("ERROR: No config.yml found. Create one with a 'jobs:' section.", err=True)
        sys.exit(1)

    with open(resolved, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    jobs = raw.get("jobs", [])
    if not jobs:
        click.echo("No jobs defined in config.yml. Add a 'jobs:' section.")
        sys.exit(0)

    default_url = raw.get("database_url")

    for i, job in enumerate(jobs, 1):
        source = job.get("source")
        table = job.get("table")
        if not source or not table:
            click.echo(f"  [WARN] Job {i} skipped: missing 'source' or 'table'.", err=True)
            continue

        url = job.get("database_url", default_url)
        engine = get_engine_from_env(url)

        click.echo(f"  Job {i}/{len(jobs)}: {source} -> {table}")
        result = df_tosql(
            df=source,
            table=table,
            engine=engine,
            if_exist=job.get("if_exist", "insert"),
            chunk=job.get("chunk", 1000),
            constraint_cols=job.get("constraint", ""),
            clean=job.get("clean", True),
            cast=job.get("cast", True),
            outlier=job.get("outlier", 0.5),
            schema_name=job.get("schema_name", ""),
        )
        click.echo(f"    [OK] success={result.success}  failed={result.failed}")

    click.echo(f"\n[OK] All {len(jobs)} jobs completed.")


@cli.command()
@click.argument("sql")
@click.option("--url", default=None, help="Database URL (or set DATABASE_URL env)")
@click.option("--limit", default=20, type=int, help="Max rows to display")
@click.option("--output", "-o", default=None, type=click.Path(), help="Export results to file (.csv or .json)")
def query(sql, url, limit, output):
    """Execute a SQL query and display results."""
    from peek import query as peek_query
    
    try:
        df = peek_query(sql, url=url)
        if output:
            from pathlib import Path
            ext = Path(output).suffix.lower()
            if ext == ".json":
                df.to_json(output, orient="records", indent=2)
            else:
                df.to_csv(output, index=False)
            click.echo(f"[OK] {len(df)} rows exported to {output}")
        else:
            click.echo(df.head(limit).to_string(index=False))
            if len(df) > limit:
                click.echo(f"\n... ({len(df) - limit} more rows)")
    except Exception as e:
        click.echo(f"[ERROR] Query failed: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--url", default=None, help="Database URL (or set DATABASE_URL env)")
@click.option("--schema", default=None, help="Database schema/namespace")
def tables(url, schema):
    """List all tables in the database."""
    from peek import tables as peek_tables
    
    try:
        table_list = peek_tables(url=url, schema=schema)
        if table_list:
            click.echo("Tables:")
            for t in table_list:
                click.echo(f"  - {t}")
        else:
            click.echo("No tables found.")
    except Exception as e:
        click.echo(f"[ERROR] Failed to list tables: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("table")
@click.option("--url", default=None, help="Database URL (or set DATABASE_URL env)")
@click.option("--schema", default=None, help="Database schema/namespace")
@click.option("--full", is_flag=True, help="Show full metadata including PKs and constraints")
def describe(table, url, schema, full):
    """Describe table schema (columns, types, constraints)."""
    from peek import describe as peek_describe, describe_full as peek_describe_full
    
    try:
        if full:
            info = peek_describe_full(table, url=url, schema=schema)
            click.echo(f"Table: {info['table']}")
            if info['schema']:
                click.echo(f"Schema: {info['schema']}")
            click.echo(f"\nColumns:")
            for col in info['columns']:
                click.echo(f"  - {col['name']}: {col['type']} (nullable={col.get('nullable', True)})")
            if info['primary_keys']:
                click.echo(f"\nPrimary Keys: {', '.join(info['primary_keys'])}")
            if info['unique_constraints']:
                click.echo(f"Unique Constraints:")
                for uc in info['unique_constraints']:
                    click.echo(f"  - {', '.join(uc)}")
        else:
            df = peek_describe(table, url=url, schema=schema)
            click.echo(df.to_string(index=False))
    except Exception as e:
        click.echo(f"[ERROR] Failed to describe table: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Config command group
# ---------------------------------------------------------------------------

def _find_config_path(config_path: str | None) -> "Path":
    """Resolve config.yml path — explicit arg > CWD > project root."""
    from pathlib import Path
    import cli as _self_mod
    if config_path:
        return Path(config_path)
    for candidate in [Path.cwd() / "config.yml", Path(__file__).resolve().parent / "config.yml"]:
        if candidate.exists():
            return candidate
    # Default to CWD if none found
    return Path.cwd() / "config.yml"


def _load_yml(path: "Path") -> dict:
    import yaml
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _save_yml(path: "Path", data: dict) -> None:
    import yaml
    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")


def _set_nested(d: dict, key_path: str, value) -> None:
    """Set a dotted key path in a nested dict. E.g. 'pipeline.chunk_size' = 5000."""
    parts = key_path.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    # Auto-convert value types
    leaf = parts[-1]
    if isinstance(value, str):
        if value.lower() in ("true", "yes"):
            value = True
        elif value.lower() in ("false", "no"):
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass  # keep as string
    d[leaf] = value


def _get_nested(d: dict, key_path: str):
    """Get a dotted key path from a nested dict."""
    parts = key_path.split(".")
    for part in parts:
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


@cli.group()
def config():
    """View and update config.yml settings."""
    pass


@config.command("show")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to config.yml")
@click.argument("key", required=False)
def config_show(config_path, key):
    """Show config.yml contents or a specific key.

    Examples:\n
      sqlpen config show\n
      sqlpen config show pipeline.chunk_size\n
      sqlpen config show jobs
    """
    from pathlib import Path
    import yaml

    path = _find_config_path(config_path)
    if not path.exists():
        click.echo(f"[WARN] No config.yml found at {path}. Run 'sqlpen config init' to create one.")
        return

    data = _load_yml(path)
    click.echo(f"Config file: {path}")
    click.echo("")

    if key:
        val = _get_nested(data, key)
        if val is None:
            click.echo(f"[WARN] Key '{key}' not found in config.yml")
        else:
            click.echo(f"{key}: {yaml.dump(val, default_flow_style=False).strip()}")
    else:
        click.echo(yaml.dump(data, default_flow_style=False, allow_unicode=True))


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to config.yml")
def config_set(key, value, config_path):
    """Set a config.yml value using dot-notation key.

    Examples:\n
      sqlpen config set pipeline.chunk_size 5000\n
      sqlpen config set pipeline.trace_sql false\n
      sqlpen config set schema_corrector.add_missing_cols true\n
      sqlpen config set database_url postgresql://user:pwd@localhost/db
    """
    path = _find_config_path(config_path)
    data = _load_yml(path)

    old_val = _get_nested(data, key)
    _set_nested(data, key, value)
    new_val = _get_nested(data, key)

    _save_yml(path, data)
    click.echo(f"[OK] {key}: {old_val!r} → {new_val!r}  (saved to {path})")


@config.command("add-job")
@click.option("--source", required=True, help="Source file path (CSV/Parquet/JSON/Excel)")
@click.option("--table", required=False, default=None, help="Target table name (inferred from source if omitted)")
@click.option("--url", default=None, help="Database URL (overrides top-level database_url)")
@click.option("--if-exist", "if_exist", default="insert",
              type=click.Choice(["insert", "replace", "upsert", "update"], case_sensitive=False),
              help="Write mode")
@click.option("--constraint", default=None, help="Unique constraint columns")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to config.yml")
def config_add_job(source, table, url, if_exist, constraint, config_path):
    """Add a new job entry to the config.yml jobs list.

    Example:\n
      sqlpen config add-job --source data/users.csv --table users --if-exist upsert --constraint id
    """
    path = _find_config_path(config_path)
    data = _load_yml(path)

    if not table:
        table = _get_default_table_name(source)
        click.echo(f"[INFO] No table specified. Inferred target table: '{table}'")

    job: dict = {"source": source, "table": table, "if_exist": if_exist}
    if url:
        job["database_url"] = url
    if constraint:
        job["constraint"] = constraint

    jobs = data.setdefault("jobs", [])
    jobs.append(job)
    _save_yml(path, data)
    click.echo(f"[OK] Job added: {source} -> {table} (mode={if_exist})  [{len(jobs)} total jobs]")


@config.command("clear-jobs")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to config.yml")
@click.confirmation_option(prompt="This will remove all jobs from config.yml. Continue?")
def config_clear_jobs(config_path):
    """Remove all jobs from config.yml."""
    path = _find_config_path(config_path)
    data = _load_yml(path)
    count = len(data.get("jobs", []))
    data["jobs"] = []
    _save_yml(path, data)
    click.echo(f"[OK] Cleared {count} job(s) from {path}")


@config.command("init")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Path to write config.yml")
@click.option("--force", is_flag=True, help="Overwrite existing config.yml")
def config_init(config_path, force):
    """Create a new config.yml with sensible defaults.

    Example:\n
      sqlpen config init\n
      sqlpen config init --force
    """
    from pathlib import Path
    path = Path(config_path) if config_path else Path.cwd() / "config.yml"

    if path.exists() and not force:
        click.echo(f"[WARN] config.yml already exists at {path}. Use --force to overwrite.")
        return

    default_config = {
        "logging": {
            "enabled": True,
            "dir": "log",
            "max_repr_len": 2000,
            "bucket_minutes": 10,
        },
        "pipeline": {
            "outlier_pct": 0.5,
            "casting": True,
            "cleaner": True,
            "profiler": True,
            "trace_sql": False,
            "schema_save_path": "schema",
            "chunk_size": 10000,
        },
        "schema_corrector": {
            "on_error": "coerce",
            "failure_threshold": 3.0,
            "validate_fk": False,
            "add_missing_cols": False,
        },
        "casting": {
            "use_transform": True,
            "infer_threshold": 0.9,
            "nan_threshold": 0.30,
            "max_null_increase": 0.1,
            "max_sample_size": 1000,
            "validate_conversions": True,
            "parallel": False,
            "chunk_size": 50000,
        },
        "jobs": [],
    }

    _save_yml(path, default_config)
    click.echo(f"[OK] config.yml created at {path}")
    click.echo("Edit it directly or use:")
    click.echo("  sqlpen config set pipeline.chunk_size 5000")
    click.echo("  sqlpen config add-job --source data.csv --table users --if-exist upsert")


# Alias: 'sqlpen harness' works the same as 'sqlpen test'
cli.add_command(test, name="harness")


def interactive():
    """Launch the interactive batch script (sqlpen_interactive.bat)."""
    import subprocess
    import sys
    from pathlib import Path

    bat_file = Path(__file__).resolve().parent / "sqlpen.bat"
    if not bat_file.exists():
        click.echo(f"[ERROR] Interactive launcher not found at {bat_file}", err=True)
        sys.exit(1)

    try:
        # Launch the batch file attached to the current console
        subprocess.run([str(bat_file)], shell=True)
    except KeyboardInterrupt:
        sys.exit(0)


def main():
    cli()


if __name__ == "__main__":
    main()
