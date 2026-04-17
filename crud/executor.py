"""
Execution engine — bulk insert/upsert with automatic row-by-row fallback.

Key pattern: attempt bulk execution first; on failure, roll back and retry
row-by-row up to a tolerance limit.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

from sqlalchemy.engine import Connection

from .types import CrudResult

logger = logging.getLogger(__name__)


def execute_with_fallback(
    conn: Connection,
    rows: List[Dict[str, Any]],
    bulk_fn: Callable[[List[Dict[str, Any]]], None],
    row_fn: Callable[[Dict[str, Any]], None],
    tolerance: int,
    strict: bool,
) -> CrudResult:
    """Execute *bulk_fn(rows)*; on failure, fall back to *row_fn* per row.

    Parameters
    ----------
    conn : Connection
        Active SQLAlchemy connection (caller manages the transaction).
    rows : list[dict]
        The data rows to execute.
    bulk_fn : callable
        Takes the full list of rows; should call conn.execute internally.
    row_fn : callable
        Takes a single row dict; called per-row during fallback.
    tolerance : int
        Maximum number of per-row failures before aborting.
    strict : bool
        If True, raise after fallback completes with failures.
    """
    if not rows:
        return CrudResult(method="none", diagnostics={"mode": "strict" if strict else "relaxed"})

    diag: Dict[str, Any] = {"mode": "strict" if strict else "relaxed", "fallback_used": False}

    # --- attempt bulk inside a SAVEPOINT ---
    try:
        nested = conn.begin_nested()
        try:
            bulk_fn(rows)
            nested.commit()
        except Exception as bulk_err:
            nested.rollback()
            raise bulk_err

        return CrudResult(
            total=len(rows),
            success=len(rows),
            method="bulk",
            diagnostics=diag,
        )
    except Exception as bulk_err:
        diag["fallback_used"] = True
        diag["bulk_error"] = f"{type(bulk_err).__name__}: {bulk_err}"

    # --- row-by-row fallback ---
    success = 0
    bad_rows: List[Tuple[int, Dict[str, Any], Exception]] = []
    row_errors: List[Dict[str, Any]] = []
    last_idx = 0

    for idx, row in enumerate(rows):
        last_idx = idx
        try:
            row_fn(row)
            success += 1
        except Exception as row_err:
            bad_rows.append((idx, row, row_err))
            row_errors.append({"row_index": idx, "error": f"{type(row_err).__name__}: {row_err}"})
            if len(bad_rows) >= tolerance:
                break

    diag["row_errors"] = row_errors

    result = CrudResult(
        total=len(rows),
        success=success,
        failed=len(bad_rows),
        method="row_fallback",
        diagnostics=diag,
    )

    aborted = len(bad_rows) >= tolerance
    if aborted:
        result.diagnostics["aborted"] = True
        result.diagnostics["unprocessed"] = len(rows) - (last_idx + 1)

    if bad_rows:
        messages = [
            f"row={i}, error={type(e).__name__}: {e}" for i, _, e in bad_rows[:10]
        ]
        error_msg = (
            f"Bulk failed, fallback: {success}/{len(rows)} succeeded.\n"
            f"Bulk error: {diag['bulk_error']}\n"
        )
        if aborted:
            error_msg += f"[!] Aborted after {tolerance} failures. {len(rows) - last_idx - 1} rows skipped.\n"
        error_msg += "Failing rows:\n" + "\n".join(messages)

        if strict or success == 0:
            raise RuntimeError(error_msg)
        logger.warning(error_msg)

    return result
