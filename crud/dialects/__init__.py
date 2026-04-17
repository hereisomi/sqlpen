"""
Dialect registry — auto-detect the engine's dialect and return the right handler.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from .base import BaseDialect


def get_dialect(engine: Engine) -> "BaseDialect":
    """Return a concrete BaseDialect subclass for *engine*."""
    name = engine.dialect.name.lower()

    if name in ("postgresql", "postgres"):
        from .postgres import PostgresDialect
        return PostgresDialect()
    if name == "oracle":
        from .oracle import OracleDialect
        return OracleDialect()
    if name in ("mysql", "mariadb"):
        from .mysql import MysqlDialect
        return MysqlDialect()
    if name == "sqlite":
        from .sqlite import SqliteDialect
        return SqliteDialect()
    if "mssql" in name or "sqlserver" in name:
        from .mssql import MssqlDialect
        return MssqlDialect()

    raise ValueError(f"Unsupported database dialect: {name}")
