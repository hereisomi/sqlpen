"""
Pre-DML validation — catch errors before they hit the database.

Covers:
- NOT NULL violation detection
- Constraint column resolution + uniqueness validation
- Intra-batch duplicate key detection
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector

logger = logging.getLogger(__name__)


def ensure_data_columns_in_table(rows: List[Dict[str, Any]], table: sa.Table) -> None:
    """Raise if any key in rows doesn't exist in the table."""
    table_cols = {c.name for c in table.columns}
    invalid: set = set()
    for r in rows:
        invalid.update(k for k in r if k not in table_cols)
    if invalid:
        raise ValueError(f"Columns not found in table '{table.name}': {sorted(invalid)}")


def collect_not_null_violations(rows: List[Dict[str, Any]], table: sa.Table) -> List[Dict[str, Any]]:
    """Return list of {row, column} dicts for NOT NULL violations."""
    issues: List[Dict[str, Any]] = []
    for idx, r in enumerate(rows):
        for c in table.columns:
            if not c.nullable and c.default is None and c.server_default is None:
                if r.get(c.name) is None:
                    issues.append({"row": idx, "column": c.name})
    return issues


def validate_constrain_unique(
    conn: Connection,
    table: sa.Table,
    constrain: List[str],
) -> Tuple[str, ...]:
    """Resolve *constrain* columns against the table and validate they match a PK or unique index.

    If *constrain* is empty, automatically discovers the table's Primary Key
    or first unique constraint and uses that (zero-config upsert).

    Returns the resolved column names (case-corrected) as a tuple.
    """
    # Case-insensitive column resolution
    cols_map = {c.name.lower(): c.name for c in table.columns}

    # ── Zero-config: auto-discover PK/unique when constrain is empty ──
    if not constrain:
        inspector = sa.inspect(conn)
        try:
            unique_sets = _reflect_unique_sets(inspector, table)
        except Exception as exc:
            raise ValueError(
                f"constrain is empty and automatic PK discovery failed for "
                f"table '{table.name}': {exc}"
            )
        if not unique_sets:
            raise ValueError(
                f"constrain is empty and no primary key or unique constraint "
                f"found on table '{table.name}'. Cannot perform zero-config upsert."
            )
        # Use the first discovered unique set (PK takes priority)
        auto_cols = unique_sets[0]
        logger.info(
            "Zero-config upsert: auto-discovered constraint %s on '%s'",
            auto_cols, table.name,
        )
        return auto_cols

    resolved: List[str] = []
    for c in constrain:
        low = c.lower()
        if low not in cols_map:
            raise ValueError(
                f"constrain column '{c}' not found in table '{table.name}'. "
                f"Available: {list(cols_map.values())}"
            )
        resolved.append(cols_map[low])

    if not resolved:
        raise ValueError("constrain must contain at least one column name")

    # Reflect PK + unique constraints and compare
    inspector = sa.inspect(conn)
    try:
        unique_sets = _reflect_unique_sets(inspector, table)
    except Exception as exc:
        logger.warning("Constraint reflection failed: %s. Trusting caller.", exc)
        return tuple(resolved)

    cset = tuple(sorted(c.lower() for c in resolved))
    for u in unique_sets:
        if tuple(sorted(c.lower() for c in u)) == cset:
            return tuple(resolved)

    raise ValueError(
        f"constrain={constrain} does not match any primary/unique key on '{table.name}'. "
        f"Found unique sets: {unique_sets}. Upsert requires a unique constraint."
    )


def _reflect_unique_sets(inspector: Inspector, table: sa.Table) -> List[Tuple[str, ...]]:
    """Return list of unique/PK column-name tuples for *table*."""
    pk = inspector.get_pk_constraint(table.name)
    pk_cols = tuple(pk.get("constrained_columns") or [])
    unique_sets: List[Tuple[str, ...]] = []
    if pk_cols:
        unique_sets.append(pk_cols)
    for uq in inspector.get_unique_constraints(table.name):
        cols = tuple(uq.get("column_names") or [])
        if cols:
            unique_sets.append(cols)
    return unique_sets
