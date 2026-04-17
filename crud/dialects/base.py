"""
Abstract base class for dialect-specific DML builders.

Each concrete dialect implements only `build_upsert_stmt` and `build_insert_stmt`.
The base class provides the execution loop (template method pattern).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Tuple, Union

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ..executor import execute_with_fallback
from ..normalize import chunk_iter, normalize_data
from ..types import CrudConfig, CrudResult
from ..validate import (
    collect_not_null_violations,
    ensure_data_columns_in_table,
    validate_constrain_unique,
)

logger = logging.getLogger(__name__)


@contextmanager
def _ensure_connection(eng: Union[Engine, Connection]) -> Iterator[Connection]:
    """Yield a Connection with transaction; no-op if already a Connection."""
    if isinstance(eng, Connection):
        yield eng
        return
    with eng.begin() as conn:
        yield conn


def _get_table(conn: Connection, table: Union[str, sa.Table]) -> sa.Table:
    """Resolve table name to a reflected sa.Table."""
    if isinstance(table, sa.Table):
        return table
    meta = sa.MetaData()
    return sa.Table(table, meta, autoload_with=conn)


def _write_sql_trace(label: str, table_name: str, stmt: Any, engine: Engine) -> None:
    """Best-effort write generated SQL to a debug file."""
    filename = f"{label}_{table_name}.sql"
    try:
        # Oracle/MSSQL return (text_clause, col_map) tuples
        actual_stmt = stmt[0] if isinstance(stmt, tuple) else stmt
        if hasattr(actual_stmt, "compile"):
            sql_str = str(actual_stmt.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
        else:
            sql_str = str(actual_stmt)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(sql_str)
    except Exception as exc:
        logger.debug("Could not write SQL trace to %s: %s", filename, exc)


class BaseDialect(ABC):
    """Abstract base for dialect-specific CRUD operations."""

    # --- abstract methods: subclasses MUST implement ---

    @abstractmethod
    def build_upsert_stmt(
        self, table: sa.Table, key_cols: Tuple[str, ...], sample_row: Dict[str, Any]
    ) -> Any:
        """Return a compiled/text statement for upsert."""
        ...

    @abstractmethod
    def build_insert_stmt(self, table: sa.Table) -> Any:
        """Return a compiled insert statement."""
        ...

    # --- template methods: subclasses CAN override ---

    def execute_upsert(
        self,
        engine: Engine,
        rows: List[Dict[str, Any]],
        table: Union[str, sa.Table],
        constrain: List[str],
        config: CrudConfig,
    ) -> CrudResult:
        """Full upsert pipeline: validate → chunk → execute with fallback."""
        if not rows:
            return CrudResult(diagnostics={"mode": "strict" if config.strict else "relaxed"})

        aggregate = CrudResult()

        with _ensure_connection(engine) as conn:
            tbl = _get_table(conn, table)
            ensure_data_columns_in_table(rows, tbl)

            # Validate constraint columns
            try:
                key_cols = validate_constrain_unique(conn, tbl, constrain)
            except Exception as exc:
                if config.strict:
                    raise
                logger.warning("Constraint validation failed: %s — trusting caller", exc)
                key_cols = tuple(constrain)

            # NOT NULL pre-check
            nn = collect_not_null_violations(rows, tbl)
            if nn:
                aggregate.diagnostics["not_null_violations"] = nn
                if config.strict:
                    raise ValueError(f"NOT NULL violation: {nn}")

            # Build statements
            stmt = self.build_upsert_stmt(tbl, key_cols, rows[0])
            if config.trace_sql:
                _write_sql_trace(f"{type(self).__name__}_upsert", tbl.name, stmt, engine)
            if getattr(config, "echo_sql", False):
                print(f"--- UPSERT SQL ---\n{stmt}\n------------------")

            # Chunk + execute
            def _bulk(part: List[Dict[str, Any]]) -> None:
                self._exec_upsert_bulk(conn, stmt, tbl, key_cols, part)

            def _row(row: Dict[str, Any]) -> None:
                self._exec_upsert_row(conn, tbl, key_cols, row)

            for part in chunk_iter(rows, config.chunk_size):
                chunk_result = execute_with_fallback(conn, part, _bulk, _row, config.tolerance, config.strict)
                aggregate = aggregate.merge(chunk_result)

        return aggregate

    def execute_insert(
        self,
        engine: Engine,
        rows: List[Dict[str, Any]],
        table: Union[str, sa.Table],
        config: CrudConfig,
    ) -> CrudResult:
        """Full insert pipeline: validate → chunk → execute with fallback."""
        if not rows:
            return CrudResult(diagnostics={"mode": "strict" if config.strict else "relaxed"})

        aggregate = CrudResult()

        with _ensure_connection(engine) as conn:
            tbl = _get_table(conn, table)
            ensure_data_columns_in_table(rows, tbl)

            nn = collect_not_null_violations(rows, tbl)
            if nn:
                aggregate.diagnostics["not_null_violations"] = nn
                if config.strict:
                    raise ValueError(f"NOT NULL violation: {nn}")

            stmt = self.build_insert_stmt(tbl)
            if config.trace_sql:
                _write_sql_trace(f"{type(self).__name__}_insert", tbl.name, stmt, engine)
            if getattr(config, "echo_sql", False):
                print(f"--- INSERT SQL ---\n{stmt}\n------------------")

            def _bulk(part: List[Dict[str, Any]]) -> None:
                conn.execute(stmt, part)

            def _row(row: Dict[str, Any]) -> None:
                conn.execute(stmt, [row])

            for part in chunk_iter(rows, config.chunk_size):
                chunk_result = execute_with_fallback(conn, part, _bulk, _row, config.tolerance, config.strict)
                aggregate = aggregate.merge(chunk_result)

        return aggregate

    # --- default bulk/row implementations (can be overridden per dialect) ---

    def _exec_upsert_bulk(
        self, conn: Connection, stmt: Any, table: sa.Table,
        key_cols: Tuple[str, ...], rows: List[Dict[str, Any]],
    ) -> None:
        """Default: execute the upsert statement with all rows."""
        conn.execute(stmt, rows)

    def _exec_upsert_row(
        self, conn: Connection, table: sa.Table,
        key_cols: Tuple[str, ...], row: Dict[str, Any],
    ) -> None:
        """Default: rebuild the upsert statement for one row and execute."""
        stmt = self.build_upsert_stmt(table, key_cols, row)
        conn.execute(stmt, [row])
