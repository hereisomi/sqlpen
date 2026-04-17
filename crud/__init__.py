"""
SqlPen CRUD — Production-grade insert / upsert / update for 5 SQL dialects.

Public API
----------
- ``auto_insert(engine, data, table, ...)``
- ``auto_upsert(engine, data, table, constrain, ...)``
- ``auto_update(engine, data, table, where, ...)``

All functions accept DataFrame, dict, or list[dict] and return a
:class:`~.types.CrudResult` dataclass.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

from utils.logger import log_call

from .dialects import get_dialect
from .normalize import normalize_data, normalize_to_df
from .schema import SchemaAligner
from .types import CrudConfig, CrudResult

logger = logging.getLogger(__name__)

__all__ = [
    "auto_insert",
    "auto_upsert",
    "auto_update",
    "CrudConfig",
    "CrudResult",
]


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

@log_call
def auto_upsert(
    engine: Engine,
    data: Any,
    table: Union[str, sa.Table],
    constrain: Optional[List[str]] = None,
    *,
    config: CrudConfig | None = None,
    **overrides: Any,
) -> CrudResult:
    """Insert-or-update rows matched by *constrain* columns.

    Parameters
    ----------
    engine : sqlalchemy.Engine
    data : DataFrame | dict | list[dict]
    table : str or sa.Table
    constrain : list[str]
        Column(s) that form the unique key for the upsert.
    config : CrudConfig, optional
    **overrides
        Any CrudConfig field can be passed as a keyword override.
    """
    cfg = _merge_config(config, overrides)
    table_name = table.name if isinstance(table, sa.Table) else table

    df = _prepare_df(data)
    df = SchemaAligner(engine, config=cfg).align(df, table_name)
    rows = normalize_data(df)

    dialect = get_dialect(engine)
    return dialect.execute_upsert(engine, rows, table_name, constrain or [], cfg)


@log_call
def auto_insert(
    engine: Engine,
    data: Any,
    table: Union[str, sa.Table],
    *,
    config: CrudConfig | None = None,
    **overrides: Any,
) -> CrudResult:
    """Append rows to *table*.

    Parameters
    ----------
    engine : sqlalchemy.Engine
    data : DataFrame | dict | list[dict]
    table : str or sa.Table
    config : CrudConfig, optional
    """
    cfg = _merge_config(config, overrides)
    table_name = table.name if isinstance(table, sa.Table) else table

    df = _prepare_df(data)
    df = SchemaAligner(engine, config=cfg).align(df, table_name)
    rows = normalize_data(df)

    dialect = get_dialect(engine)
    return dialect.execute_insert(engine, rows, table_name, cfg)


@log_call
def auto_update(
    engine: Engine,
    data: Any,
    table: Union[str, sa.Table],
    where: List[Union[str, Tuple[str, str, Any]]],
    *,
    expression: str | None = None,
    config: CrudConfig | None = None,
    **overrides: Any,
) -> CrudResult:
    """Update rows matching the *where* conditions.

    Parameters
    ----------
    engine : sqlalchemy.Engine
    data : DataFrame | dict | list[dict]
    table : str or sa.Table
    where : list of conditions
        Each element is either:
        - a tuple ``(column, operator, value)`` — e.g. ``("id", "=", "?")``
          where ``"?"`` means "use the value from the data row"
        - a string ``"column = value"`` parsed via regex
    expression : str, optional
        Combine conditions: ``"1 AND 2"`` or ``"1 AND (2 OR 3)"`` where
        digits refer to the 1-based index of each condition in *where*.
    config : CrudConfig, optional
    """
    cfg = _merge_config(config, overrides)
    table_name = table.name if isinstance(table, sa.Table) else table

    df = _prepare_df(data)
    df = SchemaAligner(engine, config=cfg).align(df, table_name)
    records = normalize_data(df)

    if not records:
        return CrudResult(diagnostics={"mode": "strict" if cfg.strict else "relaxed"})

    dialect_name = engine.dialect.name.lower()
    total_updated = 0

    with _ensure_conn(engine) as conn:
        for idx, record in enumerate(records):
            sql, params = _build_update_sql(record, table_name, where, dialect_name, expression)
            if cfg.trace_sql:
                logger.debug("UPDATE SQL [row %d]: %s | params=%s", idx, sql, params)
            if cfg.echo_sql:
                print(f"--- UPDATE SQL ---\n{sql}\n------------------")
            result = conn.execute(sa.text(sql), params)
            total_updated += result.rowcount if result.rowcount is not None else 0

    return CrudResult(
        total=len(records),
        success=total_updated,
        method="row_update",
        diagnostics={"mode": "strict" if cfg.strict else "relaxed"},
    )


# ═══════════════════════════════════════════════════════════════════════════
# WHERE clause query builder (preserved complexity)
# ═══════════════════════════════════════════════════════════════════════════

# Regex for parsing string conditions like "col >= 42"
_WD = r"[A-Za-z_][\w$]*"
_OP = r"BETWEEN|IN|LIKE|<=|>=|!=|=|>|<"
_RX = re.compile(rf"^(?P<field>{_WD})\s*(?P<op>{_OP})\s*(?P<val>.+)$", re.I)


def _parse_condition(cond: Any, idx: int) -> Dict[str, Any]:
    """Parse a single condition into {field, operator, value, id}."""
    if isinstance(cond, tuple):
        if len(cond) != 3:
            raise ValueError("Tuple condition must be (field, operator, value)")
        return {"field": cond[0], "operator": cond[1], "value": cond[2], "id": idx}
    if isinstance(cond, str):
        m = _RX.match(cond.strip())
        if not m:
            raise ValueError(f"Invalid condition string: {cond}")
        return {
            "field": m.group("field"),
            "operator": m.group("op").upper(),
            "value": _process_value(m.group("op").upper(), m.group("val")),
            "id": idx,
        }
    if isinstance(cond, dict):
        out = dict(cond)
        out["id"] = idx
        return out
    raise TypeError(f"Unsupported condition type: {type(cond)}")


def _process_value(op: str, raw: str) -> Any:
    """Parse raw value string based on operator."""
    op = op.upper()
    if op == "IN":
        if not (raw.startswith("(") and raw.endswith(")")):
            raise ValueError("IN values must be wrapped in parentheses")
        import csv, io
        inner = raw[1:-1].strip()
        if not inner:
            return []
        reader = csv.reader(io.StringIO(inner), quotechar="'", skipinitialspace=True)
        try:
            return next(reader)
        except StopIteration:
            return []
    if op == "BETWEEN":
        parts = re.split(r"\bAND\b", raw, flags=re.I)
        if len(parts) != 2:
            raise ValueError("BETWEEN requires exactly two values")
        return [p.strip().strip("'\"") for p in parts]
    return raw.strip().strip("'\"")


def _escape_ident(name: str, dialect: str) -> str:
    """Quick identifier escape for UPDATE statements."""
    if dialect in ("mysql", "mariadb"):
        return f"`{name}`"
    if dialect == "mssql":
        return f"[{name}]"
    return f'"{name}"'


def _build_single_condition(cd: Dict[str, Any], params: Dict[str, Any], dialect: str) -> str:
    """Build a single SQL condition with parameterised binds."""
    op, field, val, cid = cd["operator"], cd["field"], cd["value"], cd["id"]
    esc = _escape_ident(field, dialect)

    if op == "BETWEEN":
        val = val if isinstance(val, (list, tuple)) and len(val) == 2 else [val, val]
        p1, p2 = f"{field}_{cid}_0", f"{field}_{cid}_1"
        params[p1], params[p2] = val[0], val[1]
        return f"{esc} BETWEEN :{p1} AND :{p2}"

    if op == "IN":
        val = val if isinstance(val, (list, tuple)) else [val]
        placeholders = []
        for i, v in enumerate(val):
            pn = f"{field}_{cid}_{i}"
            placeholders.append(f":{pn}")
            params[pn] = v
        return f"{esc} IN ({', '.join(placeholders)})"

    if op == "LIKE":
        pn = f"{field}_{cid}"
        # Escape LIKE wildcards in the value
        lit = str(val).replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
        params[pn] = f"%{lit}%"
        esc_clause = " ESCAPE '\\'" if dialect in ("postgresql", "oracle", "mysql", "mssql") else ""
        return f"{esc} LIKE :{pn}{esc_clause}"

    # Simple comparison: = != > >= < <=
    pn = f"{field}_{cid}"
    params[pn] = val
    return f"{esc} {op} :{pn}"


def _build_where(conditions: List[Any], expression: str | None, dialect: str) -> Tuple[str, Dict[str, Any]]:
    """Build full WHERE clause from a list of conditions + optional expression."""
    parsed = [_parse_condition(c, i + 1) for i, c in enumerate(conditions)]
    params: Dict[str, Any] = {}
    q_map: Dict[int, str] = {}
    for cd in parsed:
        q_map[cd["id"]] = _build_single_condition(cd, params, dialect)

    if not expression:
        return " AND ".join(q_map.values()), params

    # Replace digit tokens with actual SQL fragments
    def _repl(m: re.Match) -> str:
        idx = int(m.group(0))
        return q_map.get(idx, m.group(0))

    return re.sub(r"\b\d+\b", _repl, expression), params


def _build_update_sql(
    record: Dict[str, Any],
    table: str,
    where: List[Any],
    dialect: str,
    expression: str | None,
) -> Tuple[str, Dict[str, Any]]:
    """Build a full UPDATE … SET … WHERE … statement for one record."""

    # Resolve placeholder values ("?") from the record
    resolved_where: List[Any] = []
    for cond in where:
        if isinstance(cond, tuple) and len(cond) == 3:
            field, op, val = cond
            if isinstance(val, str) and val.strip() == "?":
                if field not in record:
                    raise ValueError(
                        f"Placeholder '?' used for field '{field}', but it is "
                        f"missing from the data record (available keys: "
                        f"{list(record.keys())})"
                    )
                val = record[field]
            resolved_where.append((field, op, val))
        else:
            resolved_where.append(cond)

    where_sql, where_params = _build_where(resolved_where, expression, dialect)

    # Build SET clause from the record
    tbl_esc = ".".join(_escape_ident(p, dialect) for p in table.split("."))
    set_parts = []
    set_params: Dict[str, Any] = {}
    for col, val in record.items():
        col_esc = _escape_ident(col, dialect)
        pname = f"u_{col}"
        set_parts.append(f"{col_esc} = :{pname}")
        set_params[pname] = val

    set_params.update(where_params)
    sql = f"UPDATE {tbl_esc} SET {', '.join(set_parts)} WHERE {where_sql}"
    return sql, set_params


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _prepare_df(data: Any) -> "pd.DataFrame":
    """Normalise input to DataFrame."""
    if pd is None:
        raise RuntimeError("pandas is required")
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, Mapping):
        return pd.DataFrame([dict(data)])
    return pd.DataFrame(data)


def _merge_config(config: CrudConfig | None, overrides: Dict[str, Any]) -> CrudConfig:
    """Build final config by merging explicit CrudConfig + keyword overrides."""
    if config is None:
        config = CrudConfig()
    if overrides:
        import dataclasses
        valid = {f.name for f in dataclasses.fields(CrudConfig)}
        for k, v in overrides.items():
            if k in valid:
                setattr(config, k, v)
    return config


@contextmanager
def _ensure_conn(eng: Union[Engine, Connection]) -> Iterator[Connection]:
    """Yield a Connection with transaction."""
    if isinstance(eng, Connection):
        yield eng
        return
    with eng.begin() as conn:
        yield conn
