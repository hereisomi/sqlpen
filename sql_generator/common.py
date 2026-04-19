from __future__ import annotations

from contextlib import contextmanager
from itertools import islice
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.reflection import Inspector

try:
    import pandas as pd
except Exception:
    pd = None


Row = Dict[str, Any]
Rows = List[Row]
DataItem = Mapping[str, Any]
DataLike = Union[Sequence[DataItem], DataItem, "pd.DataFrame"]


def normalize_data(data: DataLike) -> Rows:
    if pd is not None and isinstance(data, pd.DataFrame):
        df = data.astype(object).where(data.notna(), None)
        return df.to_dict(orient="records")
    if isinstance(data, Mapping):
        return [dict(data)]
    if isinstance(data, Sequence):
        return [dict(r) for r in data]
    raise TypeError("data must be DataFrame, dict, or sequence of dict")


def chunk_rows(rows: Rows, size: int) -> Iterable[Rows]:
    it = iter(rows)
    while True:
        part = list(islice(it, size))
        if not part:
            return
        yield part


@contextmanager
def ensure_connection(engine_or_conn: Union[Engine, Connection]) -> Iterable[Connection]:
    if isinstance(engine_or_conn, Connection):
        yield engine_or_conn
        return
    with engine_or_conn.begin() as conn:
        yield conn


def get_table(conn: Connection, table: Union[str, sa.Table]) -> sa.Table:
    if isinstance(table, sa.Table):
        return table
    meta = sa.MetaData()
    return sa.Table(table, meta, autoload_with=conn)


def validate_columns_exist(rows: Rows, table: sa.Table) -> None:
    table_cols = {c.name.lower() for c in table.columns}
    invalid: set = set()
    for r in rows:
        for k in r.keys():
            if str(k).lower() not in table_cols:
                invalid.add(k)
    if invalid:
        raise ValueError(f"Columns not found in table '{table.name}': {sorted(invalid)}")


def reflect_unique_sets(inspector: Inspector, table: sa.Table) -> List[Tuple[str, ...]]:
    unique_sets: List[Tuple[str, ...]] = []
    pk = inspector.get_pk_constraint(table.name)
    pk_cols = tuple(pk.get("constrained_columns") or [])
    if pk_cols:
        unique_sets.append(pk_cols)
    for uq in inspector.get_unique_constraints(table.name):
        cols = tuple(uq.get("column_names") or [])
        if cols:
            unique_sets.append(cols)
    for ix in inspector.get_indexes(table.name):
        if ix.get("unique"):
            cols = tuple(ix.get("column_names") or [])
            if cols:
                unique_sets.append(cols)
    return unique_sets


def validate_constrain_unique(conn: Connection, table: sa.Table, constrain: List[str]) -> Tuple[str, ...]:
    cols_map = {c.name.lower(): c.name for c in table.columns}
    resolved: List[str] = []
    for c in constrain:
        key = str(c).lower()
        if key not in cols_map:
            raise ValueError(f"constrain column '{c}' not found in '{table.name}'")
        resolved.append(cols_map[key])
    if not resolved:
        raise ValueError("constrain must contain at least one column")
    inspector = sa.inspect(conn)
    try:
        unique_sets = reflect_unique_sets(inspector, table)
    except Exception:
        return tuple(resolved)
    target = tuple(sorted(x.lower() for x in resolved))
    for u in unique_sets:
        if tuple(sorted(x.lower() for x in u)) == target:
            return tuple(resolved)
    raise ValueError(f"constrain={constrain} does not match any PK/UNIQUE on '{table.name}'")


def exec_with_row_isolation(
    rows: Rows,
    bulk_exec: Callable[[Rows], None],
    row_exec: Callable[[Row], None],
    tolerance: int
) -> Dict[str, Any]:
    if not rows:
        return {"total": 0, "success": 0, "failed": 0, "method": "none"}

    try:
        bulk_exec(rows)
        return {"total": len(rows), "success": len(rows), "failed": 0, "method": "bulk"}
    except Exception as bulk_err:
        success = 0
        failed = 0
        idx = -1
        bad: List[Tuple[int, str]] = []
        for idx, r in enumerate(rows):
            try:
                row_exec(r)
                success += 1
            except Exception as row_err:
                failed += 1
                bad.append((idx, f"{type(row_err).__name__}: {row_err}"))
                if failed >= tolerance:
                    break
        if success == 0:
            raise RuntimeError(_format_bulk_failure(bulk_err, bad)) from bulk_err
        stats: Dict[str, Any] = {"total": len(rows), "success": success, "failed": failed, "method": "lazy_fallback"}
        if failed >= tolerance:
            stats["aborted"] = True
            stats["unprocessed"] = len(rows) - (idx + 1)
        if bad:
            stats["errors"] = bad[:10]
        return stats


def _format_bulk_failure(bulk_err: Exception, bad: List[Tuple[int, str]]) -> str:
    lines = [
        "Bulk operation failed; lazy fallback also failed.",
        f"Bulk error: {type(bulk_err).__name__}: {bulk_err}",
    ]
    if bad:
        lines.append("Row failures:")
        for i, err in bad[:10]:
            lines.append(f"  row_index={i}: {err}")
    return "\n".join(lines)


def write_sql_trace(func: str, table: str, stmt: Any, engine: Engine) -> None:
    text = _compile_sql(stmt, engine)
    _safe_write(f"{func}_{table}.txt", text)


def _compile_sql(stmt: Any, engine: Engine) -> str:
    if hasattr(stmt, "compile"):
        compiled = stmt.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
        return str(compiled)
    return str(stmt)


def _safe_write(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
