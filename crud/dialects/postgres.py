"""PostgreSQL dialect — INSERT … ON CONFLICT … DO UPDATE."""
from __future__ import annotations

from typing import Any, Dict, Tuple

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from .base import BaseDialect


class PostgresDialect(BaseDialect):

    def build_upsert_stmt(
        self, table: sa.Table, key_cols: Tuple[str, ...], sample_row: Dict[str, Any]
    ) -> sa.sql.dml.Insert:
        ins = postgresql.insert(table)
        update_cols = {c: ins.excluded[c] for c in sample_row if c not in key_cols}
        if not update_cols:
            return ins.on_conflict_do_nothing(index_elements=list(key_cols))
        return ins.on_conflict_do_update(index_elements=list(key_cols), set_=update_cols)

    def build_insert_stmt(self, table: sa.Table) -> sa.sql.dml.Insert:
        return table.insert()
