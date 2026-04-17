"""MySQL / MariaDB dialect — INSERT … ON DUPLICATE KEY UPDATE."""
from __future__ import annotations

from typing import Any, Dict, Tuple

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from .base import BaseDialect


class MysqlDialect(BaseDialect):

    def build_upsert_stmt(
        self, table: sa.Table, key_cols: Tuple[str, ...], sample_row: Dict[str, Any]
    ) -> sa.sql.dml.Insert:
        ins = mysql.insert(table)
        update_cols = {c: ins.inserted[c] for c in sample_row if c not in key_cols}
        if not update_cols:
            # No non-key columns to update; set first key col = itself as no-op
            first_key = key_cols[0]
            return ins.on_duplicate_key_update(**{first_key: ins.inserted[first_key]})
        return ins.on_duplicate_key_update(**update_cols)

    def build_insert_stmt(self, table: sa.Table) -> sa.sql.dml.Insert:
        return table.insert()
