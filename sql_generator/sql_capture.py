from __future__ import annotations

import logging
import datetime as dt
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass
class CapturedStatement:
    sql: str
    params: Any
    operation: str          # INSERT / UPDATE / MERGE / SELECT / etc.
    table: str
    timestamp: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    def __str__(self) -> str:
        return f"[{self.timestamp}] {self.operation} {self.table}\n  SQL: {self.sql}\n  PARAMS: {self.params}"


class SqlCapture:
    """
    Captures all SQL statements executed through a SQLAlchemy engine.

    Usage — context manager (auto attach/detach):
        with SqlCapture(engine) as cap:
            router.insert(engine, df, "employees")
        print(cap.statements)

    Usage — manual:
        cap = SqlCapture(engine)
        cap.attach()
        router.upsert(engine, df, "employees", constrain=["emp_id"])
        cap.detach()
        cap.print_all()
        cap.save("captured.sql")
    """

    def __init__(self, engine: Engine, log: bool = False):
        self.engine = engine
        self.log = log
        self.statements: List[CapturedStatement] = []
        self._attached = False

    def attach(self) -> None:
        if self._attached:
            return
        sa.event.listen(self.engine, "before_cursor_execute", self._handler, named=True)
        self._attached = True

    def detach(self) -> None:
        if not self._attached:
            return
        sa.event.remove(self.engine, "before_cursor_execute", self._handler)
        self._attached = False

    def clear(self) -> None:
        self.statements.clear()

    def __enter__(self) -> "SqlCapture":
        self.attach()
        return self

    def __exit__(self, *_) -> None:
        self.detach()

    # ── output helpers ────────────────────────────────────────────────────────

    def print_all(self) -> None:
        for s in self.statements:
            print(s)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for s in self.statements:
                f.write(str(s) + "\n\n")

    def filter(self, operation: str) -> List[CapturedStatement]:
        """Return only statements matching operation (INSERT, UPDATE, MERGE, etc.)"""
        op = operation.upper()
        return [s for s in self.statements if s.operation == op]

    @property
    def sql_only(self) -> List[str]:
        """Just the SQL strings."""
        return [s.sql for s in self.statements]

    # ── internal ──────────────────────────────────────────────────────────────

    def _handler(self, conn, cursor, statement, parameters, context, executemany, **kw):
        op = _detect_operation(statement)
        table = _detect_table(statement)
        captured = CapturedStatement(
            sql=statement.strip(),
            params=parameters,
            operation=op,
            table=table,
        )
        self.statements.append(captured)
        if self.log:
            logger.debug(str(captured))


def _detect_operation(sql: str) -> str:
    first = sql.strip().split()[0].upper() if sql.strip() else "UNKNOWN"
    return first


def _detect_table(sql: str) -> str:
    """Best-effort table name extraction from SQL string."""
    import re
    sql_upper = sql.upper().strip()
    patterns = [
        r"INSERT\s+INTO\s+(\S+)",
        r"UPDATE\s+(\S+)",
        r"MERGE\s+INTO\s+(\S+)",
        r"DELETE\s+FROM\s+(\S+)",
        r"SELECT\s+.+\s+FROM\s+(\S+)",
    ]
    for pat in patterns:
        m = re.search(pat, sql_upper)
        if m:
            return m.group(1).strip("\"'[]`")
    return "unknown"


# ── convenience context manager ───────────────────────────────────────────────

@contextmanager
def capture_sql(engine: Engine, log: bool = False):
    """
    Shorthand context manager.

    Example:
        with capture_sql(engine) as cap:
            router.insert(engine, df, "employees")
        cap.print_all()
    """
    cap = SqlCapture(engine, log=log)
    cap.attach()
    try:
        yield cap
    finally:
        cap.detach()
