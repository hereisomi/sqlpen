"""Oracle dialect — MERGE INTO … USING (SELECT … FROM DUAL)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..sanitize import escape_identifier
from .base import BaseDialect


class OracleDialect(BaseDialect):

    def build_upsert_stmt(
        self, table: sa.Table, key_cols: Tuple[str, ...], sample_row: Dict[str, Any]
    ) -> Tuple[sa.TextClause, Dict[str, str]]:
        """Build Oracle MERGE as raw SQL with sanitised bind parameters.

        Returns (text_clause, col_to_param_mapping) so that the execution
        methods can remap row dicts before passing them to ``conn.execute``.
        """
        src_cols = list(sample_row.keys())
        esc = lambda c: escape_identifier(c, "oracle")

        # Build sanitised bind parameter names: p0, p1, p2, ...
        col_to_param: Dict[str, str] = {c: f"p{i}" for i, c in enumerate(src_cols)}

        # Source: SELECT :p0 AS "COL", :p1 AS "COL2" FROM DUAL
        select_parts = [f":{col_to_param[c]} AS {esc(c)}" for c in src_cols]
        src_sql = f"SELECT {', '.join(select_parts)} FROM DUAL"

        # ON clause
        tbl_esc = esc(table.name)
        on_sql = " AND ".join(f"tgt.{esc(c)} = src.{esc(c)}" for c in key_cols)

        # UPDATE SET (non-key columns)
        key_lower = {k.lower() for k in key_cols}
        update_cols = [c for c in src_cols if c.lower() not in key_lower]
        update_sql = ", ".join(f"tgt.{esc(c)} = src.{esc(c)}" for c in update_cols) if update_cols else None

        # INSERT
        ins_cols = ", ".join(esc(c) for c in src_cols)
        ins_vals = ", ".join(f"src.{esc(c)}" for c in src_cols)

        merge = f"MERGE INTO {tbl_esc} tgt USING ({src_sql}) src ON ({on_sql})"
        if update_sql:
            merge += f" WHEN MATCHED THEN UPDATE SET {update_sql}"
        merge += f" WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})"

        return sa.text(merge), col_to_param

    def build_insert_stmt(self, table: sa.Table) -> sa.sql.dml.Insert:
        return table.insert()

    def _remap_rows(
        self, col_to_param: Dict[str, str], rows: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remap row dicts from column names to sanitised param keys."""
        return [{col_to_param[k]: v for k, v in row.items() if k in col_to_param} for row in rows]

    # Override: Oracle MERGE with sa.text needs remapped bind params.
    def _exec_upsert_bulk(
        self, conn: Connection, stmt: Any, table: sa.Table,
        key_cols: Tuple[str, ...], rows: List[Dict[str, Any]],
    ) -> None:
        """Oracle MERGE — execute with remapped param keys."""
        text_stmt, col_to_param = stmt
        conn.execute(text_stmt, self._remap_rows(col_to_param, rows))

    def _exec_upsert_row(
        self, conn: Connection, table: sa.Table,
        key_cols: Tuple[str, ...], row: Dict[str, Any],
    ) -> None:
        """Rebuild MERGE for one row and execute with remapped params."""
        text_stmt, col_to_param = self.build_upsert_stmt(table, key_cols, row)
        conn.execute(text_stmt, self._remap_rows(col_to_param, [row]))
