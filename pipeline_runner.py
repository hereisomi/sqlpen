"""
Pipeline runner — orchestrates the INSERT → UPSERT → UPDATE workflow.

Ties together:
- ``utils.harness.CrudTestHarness`` for deterministic test data generation
- ``crud.auto_insert / auto_upsert / auto_update`` for DML execution
- ``crud.fingerprint`` for schema drift detection

Generates a ``<csv_name>_harness.txt`` report file with:
- All SQL queries executed (DML + verification SELECTs)
- Step-by-step results (success/fail counts, elapsed time)
- Root cause analysis for failures with actionable fix recommendations

Usage::

    from pipeline.csv_harness import PipelineRunner

    runner = PipelineRunner(engine, df, table="users",
                            pk_cols="id", constraint_cols="email")
    report = runner.run()
    print(report.summary())
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Engine

from pipeline import run as pipeline_run
from utils.fingerprint import build_fingerprint, diff_fingerprints, SchemaFingerprint, SchemaDiff
from utils.harness import CrudTestHarness, QuerySpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Root cause patterns — maps regex on error messages to human diagnostics
# ---------------------------------------------------------------------------
_ROOT_CAUSE_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"value too long.+varying\((\d+)\)", re.I),
        "VARCHAR overflow",
        "Data contains values longer than the column allows. "
        "Fix: Truncate the data or ALTER the column to a wider VARCHAR.",
    ),
    (
        re.compile(r"ORA-12899.+\"(.+?)\".+\(actual: (\d+), maximum: (\d+)\)", re.I),
        "Oracle VARCHAR2 overflow",
        "Data exceeds the column's byte/char limit. "
        "Fix: Truncate the data or ALTER column to VARCHAR2({actual_len}).",
    ),
    (
        re.compile(r"not[- ]null.+constraint|cannot.+null|null.+not allowed", re.I),
        "NOT NULL violation",
        "One or more rows have NULL in a NOT NULL column. "
        "Fix: Provide default values, drop rows with NULLs, or ALTER the column to allow NULL.",
    ),
    (
        re.compile(r"duplicate.+key|unique.+constraint|violates.+unique", re.I),
        "Duplicate key violation",
        "Data contains duplicate values in the constraint/PK columns. "
        "Fix: Deduplicate the source data, or use UPSERT mode instead of INSERT.",
    ),
    (
        re.compile(r"foreign.+key.+constraint|referential.+integrity", re.I),
        "Foreign key violation",
        "Data references rows in a parent table that don't exist. "
        "Fix: Insert parent records first, or disable FK checks during load.",
    ),
    (
        re.compile(r"data.+truncat|string.+truncat|truncation", re.I),
        "Data truncation",
        "String or numeric data was truncated during insertion. "
        "Fix: Widen the target column or trim the source data.",
    ),
    (
        re.compile(r"numeric.+overflow|out of range|arithmetic overflow", re.I),
        "Numeric overflow",
        "A numeric value exceeds the column's precision/scale. "
        "Fix: Use a wider numeric type (e.g., BIGINT or DOUBLE PRECISION).",
    ),
    (
        re.compile(r"invalid.+input.+syntax.+type|cannot.+cast|conversion.+failed", re.I),
        "Type casting failure",
        "A value cannot be cast to the target column type. "
        "Fix: Clean the source data (e.g. remove non-numeric chars from numeric columns).",
    ),
    (
        re.compile(r"column.+not found|unknown.+column|no.+such.+column", re.I),
        "Column mismatch",
        "Data has columns that don't exist in the target table. "
        "Fix: Check column names, enable add_missing_cols, or sanitize with clean=True.",
    ),
    (
        re.compile(r"table.+not.+found|relation.+does.+not.+exist|no.+such.+table", re.I),
        "Table not found",
        "The target table does not exist in the database. "
        "Fix: Create the table first, or set if_exist='insert' which auto-creates.",
    ),
]


def _diagnose_error(error_str: str) -> Tuple[str, str]:
    """Match an error string against known patterns and return (cause, recommendation)."""
    for pattern, cause, recommendation in _ROOT_CAUSE_PATTERNS:
        if pattern.search(error_str):
            return cause, recommendation
    return "Unknown", f"Error: {error_str}\nFix: Inspect the failing rows manually and check DB logs."


def _extract_row_diagnostics(crud_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract per-row failure details from result diagnostics."""
    # Note: Full row-level diagnostic extraction from pipeline.run() 
    # (AnalysisReport, ExecutionReport) is not yet supported in sqlsql's harness.
    return []


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    """Result of a single pipeline step (INSERT / UPSERT / UPDATE)."""

    step: str
    crud_result: Dict[str, Any]
    validation_passed: bool = False
    validation_error: Optional[str] = None
    elapsed_s: float = 0.0
    rows_sent: int = 0
    verification_sql: Optional[str] = None
    verification_params: Optional[Dict[str, Any]] = None
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pipeline report
# ---------------------------------------------------------------------------
@dataclass
class PipelineReport:
    """Aggregate report for the full INSERT → UPSERT → UPDATE cycle."""

    table: str
    steps: List[StepResult] = field(default_factory=list)
    fingerprint_before: Optional[SchemaFingerprint] = None
    fingerprint_after: Optional[SchemaFingerprint] = None
    schema_diff: Optional[SchemaDiff] = None
    total_elapsed_s: float = 0.0

    @property
    def all_passed(self) -> bool:
        return all(s.validation_passed for s in self.steps)

    def summary(self) -> str:
        lines = [
            "=" * 72,
            f"Pipeline Report: {self.table}",
            "=" * 72,
        ]
        for s in self.steps:
            status = "✓ PASS" if s.validation_passed else f"✗ FAIL: {s.validation_error}"
            rows_aff = s.crud_result.get("result", 0)
            if isinstance(rows_aff, dict): rows_aff = rows_aff.get("rows_affected", 0)
            lines.append(
                f"  [{s.step:>8}]  {s.rows_sent:>6} rows  "
                f"| rows_affected={rows_aff}  "
                f"| {s.elapsed_s:.3f}s  | {status}"
            )
            # Surface root cause diagnostics
            for diag in s.diagnostics:
                lines.append(f"           └─ [{diag['root_cause']}] {diag.get('recommendation', '')}")
        if self.schema_diff and self.schema_diff.changed:
            lines.append(f"\n  Schema drift detected:\n    {self.schema_diff.summary()}")
        lines.append(f"\n  Total elapsed: {self.total_elapsed_s:.3f}s")
        verdict = "ALL STEPS PASSED" if self.all_passed else "FAILURES DETECTED"
        lines.append(f"  Verdict: {verdict}")
        lines.append("=" * 72)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------
class PipelineRunner:
    """Orchestrates the full INSERT → UPSERT → UPDATE pipeline.

    Parameters
    ----------
    engine : sqlalchemy.Engine
        Target database engine.
    source_df : pd.DataFrame
        Source data (≥ 5 rows).  The harness splits this into INSERT / UPSERT / UPDATE slices.
    table : str
        Target table name.
    pk_cols : str | list[str]
        Primary key column(s) — used for SELECT verification.
    constraint_cols : str | list[str]
        Unique constraint column(s) — used for UPSERT match and UPDATE WHERE.
    config : CrudConfig, optional
        CRUD configuration overrides.
    schema : str, optional
        Database schema (for multi-schema databases).
    validate : bool
        If True (default), run verification SELECTs after each step.
    report_path : str or Path, optional
        Path to write the harness report file. If None, auto-generated.
    clean : bool
        If True (default), sanitize column names before slicing.
    cast : bool
        If True (default), auto-cast types (dates, booleans, numerics) before slicing.
    outlier : float
        IQR outlier threshold (0 = disabled). Default 0.5.
    auto_profiling : bool
        If True, run the profiler to infer PK if not provided. Default False.
    """

    def __init__(
        self,
        engine: Engine,
        source_df: pd.DataFrame,
        table: str,
        pk_cols: str | list[str],
        constraint_cols: str | list[str],
        *,
        chunk: int = 10000,
        schema: str | None = None,
        validate: bool = True,
        report_path: str | Path | None = None,
        clean: bool = True,
        cast: bool = True,
        outlier: float = 0.5,
        auto_profiling: bool = False,
    ):
        self.engine = engine
        self.table = table
        self.schema = schema
        self.validate = validate
        self.chunk = chunk
        self.report_path = report_path
        self._clean = clean
        self._cast = cast
        self._outlier = outlier
        self._auto_profiling = auto_profiling

        # Apply the data preparation pipeline (clean → cast → outlier → profile)
        prepared_df, pk_cols, constraint_cols = self._prepare_data(
            source_df, pk_cols, constraint_cols,
        )
        self.pipeline_applied = clean or cast or (outlier > 0) or auto_profiling

        from sqlalchemy import inspect
        if not inspect(self.engine).has_table(self.table, schema=self.schema):
            from utils.ddl_create import df_ddl
            ddl_str, _, prepared_df = df_ddl(
                prepared_df, 
                self.table, 
                server=self.engine.dialect.name,
                schema=self.schema,
                pk=pk_cols
            )
            with self.engine.begin() as conn:
                for stmt in ddl_str.split(";"):
                    if stmt.strip():
                        conn.execute(text(stmt.strip()))
                # Construct unique indexes if requested
                # (Note: self.harness hasn't been created yet, so we use constraint_cols)
                if constraint_cols:
                    cols = [constraint_cols] if isinstance(constraint_cols, str) else list(constraint_cols)
                    for col in cols:
                        # Synchronize constraint col name if it was uppercased for Oracle
                        target_col = col.upper() if self.engine.dialect.name.lower() == "oracle" else col
                        idx_name = f"idx_unq_{self.table}_{target_col}"
                        idx_sql = f"CREATE UNIQUE INDEX {idx_name} ON {self.table} ({target_col})"
                        try:
                            conn.execute(text(idx_sql))
                        except Exception as exc:
                            logger.warning("Failed to construct unique index %s: %s", idx_name, exc)
            logger.info("Auto-created table '%s' for pipeline harness.", self.table)

        # Build the test harness — this validates the (possibly prepared/synchronized) data
        self.harness = CrudTestHarness(
            df_src=prepared_df,
            pk_cols=pk_cols,
            constraint_cols=constraint_cols,
            table_name=table,
        )

    # -----------------------------------------------------------------------
    # Data preparation (mirrors df_tosql pipeline)
    # -----------------------------------------------------------------------
    def _prepare_data(
        self,
        df: pd.DataFrame,
        pk_cols: str | list[str],
        constraint_cols: str | list[str],
    ) -> tuple[pd.DataFrame, list[str] | str, list[str] | str]:
        """Apply clean → cast → outlier → profile pipeline to source data.

        Returns the prepared DataFrame and possibly remapped column names.
        """
        data = df.copy()
        dialect = self.engine.dialect.name.lower()

        # Normalize to lists for remapping
        pk_list = [pk_cols] if isinstance(pk_cols, str) else list(pk_cols)
        con_list = [constraint_cols] if isinstance(constraint_cols, str) else list(constraint_cols)

        if self._clean:
            logger.info("[Harness] Sanitizing column names...")
            from utils.cleaner import quick_clean
            data = quick_clean(data)
            # Cannot automatically remap pk and constraint column names easily right now
            # Assume user gave correct names for now

        if self._cast:
            logger.info("[Harness] Auto-casting variable types...")
            from utils.casting import cast_df
            data = cast_df(data)

        if self._outlier and self._outlier > 0:
            logger.info("[Harness] Quarantining outliers (threshold=%s)...", self._outlier)
            from aligner.outliers import detect_outliers, apply_outlier_action
            outlier_meta = detect_outliers(data, columns=list(data.columns), method="iqr", iqr_factor=self._outlier)
            data, _ = apply_outlier_action(data, outlier_meta, action="nullify")

        # 4. Auto-profiling & PK inference
        if self._auto_profiling:
            logger.info("[Harness] Profiling dataframe...")
            from utils.profiler import profile_dataframe, get_pk
            df_info = profile_dataframe(data)
            if pk_list == ['id'] or not pk_list:
                data, pk_name, _ = get_pk(data, df_info)
                if pk_name:
                    pk_list = [pk_name]
                    logger.info("[Harness] Inferred Primary Key: %s", pk_list)

        return data, pk_list, con_list

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def run(self) -> PipelineReport:
        """Execute the full INSERT → UPSERT → UPDATE pipeline."""
        t0 = time.perf_counter()
        report = PipelineReport(table=self.table)

        # Schema fingerprint before
        try:
            inspector = sa.inspect(self.engine)
            report.fingerprint_before = build_fingerprint(inspector, self.table, self.schema)
        except Exception as exc:
            logger.warning("Pre-run fingerprint failed: %s", exc)

        # Step 1: INSERT
        report.steps.append(self._run_insert())

        # Step 2: UPSERT
        report.steps.append(self._run_upsert())

        # Step 3: UPDATE
        report.steps.append(self._run_update())

        # Schema fingerprint after
        try:
            inspector = sa.inspect(self.engine)
            report.fingerprint_after = build_fingerprint(inspector, self.table, self.schema)
            if report.fingerprint_before:
                report.schema_diff = diff_fingerprints(
                    report.fingerprint_before, report.fingerprint_after,
                )
        except Exception as exc:
            logger.warning("Post-run fingerprint failed: %s", exc)

        report.total_elapsed_s = round(time.perf_counter() - t0, 3)
        logger.info(report.summary())

        # Write the harness report file
        self._write_report(report)

        return report

    def run_insert_only(self) -> StepResult:
        """Execute only the INSERT step."""
        return self._run_insert()

    def run_upsert_only(self) -> StepResult:
        """Execute only the UPSERT step (assumes INSERT already ran)."""
        return self._run_upsert()

    def run_update_only(self) -> StepResult:
        """Execute only the UPDATE step (assumes INSERT + UPSERT already ran)."""
        return self._run_update()

    # -----------------------------------------------------------------------
    # Step implementations
    # -----------------------------------------------------------------------
    def _run_insert(self) -> StepResult:
        df = self.harness.insert_df
        t0 = time.perf_counter()
        logger.info("[INSERT] Sending %d rows to '%s'", len(df), self.table)

        crud_result = pipeline_run(
            engine=self.engine, df=df, table=self.table, schema=self.schema,
            mode="insert", chunk=self.chunk,
        )
        elapsed = round(time.perf_counter() - t0, 3)

        step = StepResult(
            step="INSERT",
            crud_result=crud_result,
            elapsed_s=elapsed,
            rows_sent=len(df),
            diagnostics=_extract_row_diagnostics(crud_result),
        )

        if self.validate:
            step = self._validate_step(step, self.harness.get_insert_check_query(),
                                        self.harness.validate_after_insert)
        return step

    def _run_upsert(self) -> StepResult:
        df = self.harness.upsert_df
        t0 = time.perf_counter()
        logger.info("[UPSERT] Sending %d rows to '%s'", len(df), self.table)

        crud_result = pipeline_run(
            engine=self.engine, df=df, table=self.table, schema=self.schema,
            mode="upsert", constrain=self.harness.constraint, chunk=self.chunk,
        )
        elapsed = round(time.perf_counter() - t0, 3)

        step = StepResult(
            step="UPSERT",
            crud_result=crud_result,
            elapsed_s=elapsed,
            rows_sent=len(df),
            diagnostics=_extract_row_diagnostics(crud_result),
        )

        if self.validate:
            step = self._validate_step(step, QuerySpec(sql=f"SELECT * FROM {self.table}", params={}),
                                        self.harness.validate_after_upsert)
        return step

    def _run_update(self) -> StepResult:
        df = self.harness.update_df
        t0 = time.perf_counter()
        logger.info("[UPDATE] Sending %d rows to '%s'", len(df), self.table)

        # Build WHERE conditions from constraint columns
        where_conditions = [
            (col, "=", "?") for col in self.harness.constraint
        ]

        crud_result = pipeline_run(
            engine=self.engine, df=df, table=self.table, schema=self.schema,
            mode="update", where=where_conditions, chunk=self.chunk,
        )
        elapsed = round(time.perf_counter() - t0, 3)

        step = StepResult(
            step="UPDATE",
            crud_result=crud_result,
            elapsed_s=elapsed,
            rows_sent=len(df),
            diagnostics=_extract_row_diagnostics(crud_result),
        )

        if self.validate:
            step = self._validate_step(step, QuerySpec(sql=f"SELECT * FROM {self.table}", params={}),
                                        self.harness.validate_after_full_cycle)
        return step

    # -----------------------------------------------------------------------
    # Verification
    # -----------------------------------------------------------------------
    def _validate_step(self, step: StepResult, query_spec: QuerySpec,
                       validator_fn) -> StepResult:
        """Execute a verification SELECT and run the harness validator."""
        step.verification_sql = query_spec.sql
        step.verification_params = query_spec.params

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query_spec.sql), query_spec.params)
                db_df = pd.DataFrame(result.fetchall(), columns=result.keys())

            validator_fn(db_df)
            step.validation_passed = True
            logger.info("[%s] Validation PASSED", step.step)
        except AssertionError as exc:
            step.validation_error = str(exc)
            cause, fix = _diagnose_error(str(exc))
            step.diagnostics.append({
                "type": "validation_failure",
                "error": str(exc),
                "root_cause": cause,
                "recommendation": fix,
            })
            logger.error("[%s] Validation FAILED: %s", step.step, exc)
        except Exception as exc:
            step.validation_error = f"Query/validation error: {exc}"
            cause, fix = _diagnose_error(str(exc))
            step.diagnostics.append({
                "type": "verification_error",
                "error": str(exc),
                "root_cause": cause,
                "recommendation": fix,
            })

        return step

    # -----------------------------------------------------------------------
    # Report file writer
    # -----------------------------------------------------------------------
    def _write_report(self, report: PipelineReport) -> None:
        """Write a detailed harness report to a text file."""
        if self.report_path:
            out_path = Path(self.report_path)
        else:
            out_path = Path(f"{self.table}_harness.txt")

        try:
            lines = self._build_report_lines(report)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("Harness report written to: %s", out_path)
        except Exception as exc:
            logger.warning("Failed to write harness report to %s: %s", out_path, exc)

    def _build_report_lines(self, report: PipelineReport) -> List[str]:
        """Build the full text content of the harness report."""
        sep = "=" * 72
        thin = "-" * 72
        lines: List[str] = []

        # Header
        lines.append(sep)
        lines.append(f"  SQLPEN HARNESS REPORT")
        lines.append(f"  Table:    {report.table}")
        lines.append(f"  Engine:   {self.engine.url.drivername}")
        lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(sep)

        # Data slicing summary
        lines.append("")
        lines.append("DATA SLICING SUMMARY")
        lines.append(thin)
        lines.append(f"  Source rows:       {len(self.harness.df)}")
        lines.append(f"  PK columns:        {self.harness.pk}")
        lines.append(f"  Constraint columns: {self.harness.constraint}")
        lines.append(f"  Mutable columns:   {self.harness.mutable}")
        lines.append(f"  INSERT slice:      {len(self.harness.insert_df)} rows (first 60%)")
        lines.append(f"  UPSERT slice:      {len(self.harness.upsert_df)} rows (30% overlap + 20% new)")
        lines.append(f"  UPDATE slice:      {len(self.harness.update_df)} rows (3 targeted rows)")

        # Pipeline mode
        lines.append("")
        lines.append("PIPELINE MODE")
        lines.append(thin)
        mode = "FULL (clean → cast → outlier → profile)" if self.pipeline_applied else "RAW (no preprocessing)"
        lines.append(f"  Mode:     {mode}")
        lines.append(f"  Clean:    {'ON' if self._clean else 'OFF'}")
        lines.append(f"  Cast:     {'ON' if self._cast else 'OFF'}")
        lines.append(f"  Outlier:  {'ON (threshold=' + str(self._outlier) + ')' if self._outlier > 0 else 'OFF'}")
        lines.append(f"  Profiler: {'ON' if self._auto_profiling else 'OFF'}")

        # Schema fingerprint (before)
        if report.fingerprint_before:
            lines.append("")
            lines.append("SCHEMA FINGERPRINT (BEFORE)")
            lines.append(thin)
            lines.append(f"  Hash: {report.fingerprint_before.hash}")
            for col in report.fingerprint_before.columns:
                lines.append(f"    {col.name:<30} {col.type_str:<25} nullable={col.nullable}")

        # Per-step details
        for step in report.steps:
            lines.append("")
            lines.append(f"STEP: {step.step}")
            lines.append(sep)

            # Result
            status = "PASS" if step.validation_passed else "FAIL"
            lines.append(f"  Status:   {status}")
            lines.append(f"  Rows sent: {step.rows_sent}")
            rows_aff = step.crud_result.get("result", 0)
            if isinstance(rows_aff, dict): rows_aff = rows_aff.get("rows_affected", 0)
            lines.append(f"  Rows Affected: {rows_aff}")
            lines.append(f"  Elapsed:  {step.elapsed_s:.3f}s")

            # DML diagnostics not fully extracted in pipeline
            lines.append(f"  Row Errors/Diagnostics not supported automatically yet")

            # Verification SQL
            if step.verification_sql:
                lines.append("")
                lines.append("  VERIFICATION QUERY:")
                lines.append(f"    {step.verification_sql}")
                if step.verification_params:
                    lines.append(f"    Params: {step.verification_params}")

            # Validation result
            if step.validation_error:
                lines.append("")
                lines.append("  VALIDATION ERROR:")
                lines.append(f"    {step.validation_error}")

            # Root cause analysis
            if step.diagnostics:
                lines.append("")
                lines.append("  ROOT CAUSE ANALYSIS:")
                seen_causes = set()
                for d in step.diagnostics:
                    cause_key = d.get("root_cause", "Unknown")
                    if cause_key not in seen_causes:
                        seen_causes.add(cause_key)
                        lines.append(f"    Cause: {cause_key}")
                        lines.append(f"    Fix:   {d.get('recommendation', 'N/A')}")
                        lines.append("")

        # Schema fingerprint (after) + drift
        if report.fingerprint_after:
            lines.append("")
            lines.append("SCHEMA FINGERPRINT (AFTER)")
            lines.append(thin)
            lines.append(f"  Hash: {report.fingerprint_after.hash}")

        if report.schema_diff:
            lines.append("")
            lines.append("SCHEMA DRIFT")
            lines.append(thin)
            if report.schema_diff.changed:
                lines.append(f"  {report.schema_diff.summary()}")
            else:
                lines.append("  No schema changes detected.")

        # Final verdict
        lines.append("")
        lines.append(sep)
        verdict = "ALL STEPS PASSED" if report.all_passed else "FAILURES DETECTED"
        lines.append(f"  VERDICT: {verdict}")
        lines.append(f"  Total elapsed: {report.total_elapsed_s:.3f}s")
        lines.append(sep)

        return lines


# ---------------------------------------------------------------------------
# CSV Entrypoint
# ---------------------------------------------------------------------------
def run_csv_pipeline(
    csv_path: str,
    engine: Engine,
    table: str,
    pk_cols: str | list[str],
    constraint_cols: str | list[str],
    chunk: int = 10000,
    schema: str | None = None,
    validate: bool = True,
    report_dir: str | Path | None = None,
    clean: bool = True,
    cast: bool = True,
    outlier: float = 0.5,
    auto_profiling: bool = False,
    **read_csv_kwargs
) -> PipelineReport:
    """Convenience function to run the pipeline directly against a CSV file.

    Reads the CSV into a DataFrame using pandas and feeds it directly
    into the PipelineRunner for the full INSERT → UPSERT → UPDATE cycle.

    The harness report is written to ``<csv_stem>_harness.txt`` in the
    same directory as the source CSV (or *report_dir* if specified).

    Parameters
    ----------
    clean : bool
        Sanitize column names (default True).
    cast : bool
        Auto-cast types (default True).
    outlier : float
        IQR outlier threshold, 0 to disable (default 0.5).
    auto_profiling : bool
        Infer PK from data profile (default False).
    """
    logger.info("Loading CSV harness from path: %s", csv_path)
    df = pd.read_csv(csv_path, **read_csv_kwargs)

    csv_stem = Path(csv_path).stem
    if report_dir:
        report_path = Path(report_dir) / f"{csv_stem}_harness.txt"
    else:
        report_path = Path(csv_path).parent / f"{csv_stem}_harness.txt"

    runner = PipelineRunner(
        engine=engine,
        source_df=df,
        table=table,
        pk_cols=pk_cols,
        constraint_cols=constraint_cols,
        chunk=chunk,
        schema=schema,
        validate=validate,
        report_path=report_path,
        clean=clean,
        cast=cast,
        outlier=outlier,
        auto_profiling=auto_profiling,
    )
    return runner.run()
