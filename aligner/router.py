from __future__ import annotations

from typing import Any, Dict, Optional

import sqlalchemy as sa

from . import analyze, coerce_dataframe, apply_ddl_plan, reflect_table_spec, DEFAULT_POLICIES, AlignmentPolicies


def _synchronize_df_casing(df: Any, table_spec: sa.Table | Any) -> Any:
    """Rename DF columns/dict keys to match TableSpec casing case-insensitively.

    This mirrors the casing sync used in pipeline.run so callers get consistent
    behavior when invoking aligner directly.
    """
    import pandas as pd
    from .models import TableSpec

    # Get physical column names from DB metadata
    if isinstance(table_spec, TableSpec):
        db_cols = list(table_spec.columns.keys())
    else:
        db_cols = [c.name for c in table_spec.columns]

    db_cols_lower = {c.lower(): c for c in db_cols}

    def sync_map(cols):
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


def align(
    engine: sa.engine.Engine,
    df: Any,
    table: str,
    schema: Optional[str] = None,
    *,
    policies: AlignmentPolicies | None = None,
    apply_ddl: bool = False,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Unified external caller for the aligner.

    Workflow: reflect table -> sync casing -> analyze -> optional DDL -> coerce.
    Returns analysis/coercion/DDL results plus the aligned DataFrame.
    """
    policies = policies or DEFAULT_POLICIES

    table_spec = reflect_table_spec(engine, schema, table)
    df_synced = _synchronize_df_casing(df, table_spec)

    report, plan, ddl_plan = analyze(df_synced, table_spec, policies)

    ddl_report = None
    if apply_ddl and ddl_plan.actions:
        ddl_report = apply_ddl_plan(engine, ddl_plan, dry_run=dry_run)
        if not dry_run:
            table_spec = reflect_table_spec(engine, schema, table)
            df_synced = _synchronize_df_casing(df_synced, table_spec)
            report, plan, ddl_plan = analyze(df_synced, table_spec, policies)

    df_clean, coercion_report = coerce_dataframe(df_synced, table_spec, plan, policies)

    return {
        "aligned_df": df_clean,
        "analysis_report": report,
        "coercion_report": coercion_report,
        "ddl_report": ddl_report,
        "ddl_plan": ddl_plan,
        "table_spec": table_spec,
    }
