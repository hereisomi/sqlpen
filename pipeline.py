from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from aligner import (
    AlignmentPolicies, DEFAULT_POLICIES,
    reflect_table_spec, analyze, coerce_dataframe, apply_ddl_plan,
    Severity,
)
from sql_generator import insert, upsert, update, update_track


def run(
    engine: Engine,
    df: Any,
    table: str,
    schema: str,
    *,
    mode: str = "insert",
    constrain: Optional[List[str]] = None,
    where: Optional[List[Any]] = None,
    expression: str = "",
    policies: Optional[AlignmentPolicies] = None,
    apply_ddl: bool = False,
    dry_run: bool = True,
    chunk: int = 10_000,
    tolerance: int = 5,
    trace_sql: bool = False,
) -> Dict[str, Any]:
    """
    Full aligner → sql_generator pipeline.

    Args:
        engine:     SQLAlchemy engine.
        df:         Source DataFrame / dict / list[dict].
        table:      Target table name.
        schema:     Target schema name.
        mode:       "insert" | "upsert" | "update" | "update_track".
        constrain:  Key columns for upsert (required for mode="upsert").
        where:      WHERE conditions for update / update_track.
        expression: Logical expression combining WHERE conditions.
        policies:   AlignmentPolicies (defaults used if None).
        apply_ddl:  Apply DDL changes detected by aligner.
        dry_run:    Simulate DDL only (no real changes) when apply_ddl=True.
        chunk:      Chunk size for bulk operations.
        tolerance:  Max row failures before aborting chunk.
        trace_sql:  Write SQL to trace files.

    Returns:
        Dict with keys: aligned_df, analysis_report, coercion_report,
        result, ddl_report (optional).
    """
    import pandas as pd
    
    if mode not in ("insert", "upsert", "update", "update_track"):
        raise ValueError(f"mode must be insert|upsert|update|update_track, got: {mode}")
    if mode == "upsert" and not constrain:
        raise ValueError("constrain is required for mode='upsert'")
    if mode in ("update", "update_track") and not where:
        raise ValueError("where is required for mode='update' and 'update_track'")

    policies = policies or DEFAULT_POLICIES

    # Short-circuit: empty DataFrame on insert returns immediately.
    if isinstance(df, pd.DataFrame) and df.empty and mode == "insert":
        from aligner.models import CoercionReport
        return {
            "aligned_df": df,
            "analysis_report": None,
            "coercion_report": CoercionReport(),
            "ddl_report": None,
            "result": 0,
        }

    # ── Step 1: reflect ──────────────────────────────────────────────────
    insp = sa.inspect(engine)
    if not insp.has_table(table, schema=schema):
        if apply_ddl and not dry_run:
            # Auto-create the table from DataFrame schema
            from utils.ddl_create import df_ddl
            from sqlalchemy import text
            ddl_str, _, df = df_ddl(
                df, table,
                server=engine.dialect.name,
                schema=schema,
            )
            with engine.begin() as conn:
                for stmt in ddl_str.split(";"):
                    if stmt.strip():
                        conn.execute(text(stmt.strip()))
        else:
            raise ValueError(f"Table '{table}' does not exist. Set apply_ddl=True and dry_run=False to auto-create.")

    table_spec = reflect_table_spec(engine, schema, table)

    # ── Step 1b: Synchronize Casing ─────────────────────────────────────────
    # Ensures DataFrame columns match TableSpec casing (crucial for Oracle/Postgres)
    df = _synchronize_df_casing(df, table_spec)

    # ── Step 2: analyze ──────────────────────────────────────────────────
    report, plan, ddl_plan = analyze(df, table_spec, policies)

    # For update modes, missing NOT NULL columns are fine since we only
    # modify the columns present in the DataFrame.
    errors = report.get_issues_by_severity(Severity.ERROR)
    if mode in ("update", "update_track"):
        from aligner.models import IssueCode
        errors = [e for e in errors if e.code != IssueCode.MISSING_DB_COLUMN]
    if errors:
        raise RuntimeError(
            "Alignment errors:\n" + "\n".join(f"  [{i.code.value}] {i.message}" for i in errors)
        )

    # ── Step 3: optional DDL ─────────────────────────────────────────────
    ddl_report = None
    if apply_ddl and ddl_plan.actions:
        ddl_report = apply_ddl_plan(engine, ddl_plan, dry_run=dry_run)

    # ── Step 4: coerce ───────────────────────────────────────────────────
    df_clean, coercion_report = coerce_dataframe(df, table_spec, plan, policies)

    # ── Step 5: write ────────────────────────────────────────────────────
    if mode == "insert":
        result = insert(engine, df_clean, table, chunk_size=chunk, tolerance=tolerance, trace_sql=trace_sql)

    elif mode == "upsert":
        result = upsert(engine, df_clean, table, constrain=constrain, chunk=chunk, tolerance=tolerance, trace_sql=trace_sql)

    elif mode == "update":
        result = update(engine, table, df_clean, where=where, expression=expression, trace_sql=trace_sql)

    elif mode == "update_track":
        result = update_track(engine, df_clean, table, where=where, expression=expression, schema=schema, chunk=chunk)

    return {
        "aligned_df": df_clean,
        "analysis_report": report,
        "coercion_report": coercion_report,
        "ddl_report": ddl_report,
        "result": result,
    }

def _synchronize_df_casing(df: Any, table_spec: sa.Table | Any) -> Any:
    """Rename DF columns/dict keys to match TableSpec casing case-insensitively."""
    import pandas as pd
    from aligner.models import TableSpec
    
    # Get physical column names from DB metadata
    if isinstance(table_spec, TableSpec):
        db_cols = list(table_spec.columns.keys())
    else:
        # Fallback for raw Table reflection if used
        db_cols = [c.name for c in table_spec.columns]
    
    db_cols_lower = {c.lower(): c for c in db_cols}

    def sync_map(cols: List[str]) -> Dict[str, str]:
        mapping = {}
        for c in cols:
            target = db_cols_lower.get(str(c).lower())
            if target and target != c:
                mapping[c] = target
        return mapping

    if isinstance(df, pd.DataFrame):
        mapping = sync_map(df.columns.tolist())
        if mapping:
            return df.rename(columns=mapping)
    elif isinstance(df, dict):
        mapping = sync_map(list(df.keys()))
        if mapping:
            return {mapping.get(k, k): v for k, v in df.items()}
    elif isinstance(df, list) and df and isinstance(df[0], dict):
        mapping = sync_map(list(df[0].keys()))
        if mapping:
            return [{mapping.get(k, k): v for k, v in r.items()} for r in df]
    
    return df
