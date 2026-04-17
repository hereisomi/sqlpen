from __future__ import annotations
import logging
import re
from datetime import datetime, date
from typing import Optional, List, Set, Dict, Any, Union, Tuple, Iterable
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from utils.logger import log_call, log_json, log_string

def normalize_db_type(dialect_name: str) -> str:
    """Normalize database dialect name"""
    name = dialect_name.lower()
    if name == 'postgresql':
        return 'postgres'
    return name

def get_logger(name: str) -> logging.Logger:
    """Get logger instance"""
    return logging.getLogger(name)

logger = get_logger(__name__)

class SchemaManager:
    TIMESTAMP_PATTERNS = [
        'time', 'date', 'period', 'event', 'load', 'update', 'created',
        'modified', 'sync', 'etl', 'process', 'run', 'txn', 'transaction',
        'timestamp', 'dt', 'ts'
    ]
    HIGH_PRIORITY_TIMESTAMP = [
        'etl_update_dt', 'update_dt', 'last_update_dt', 'load_date',
        'etl_load_dt', 'dw_load_dt', 'event_time', 'txn_date', 'process_dt'
    ]

    def __init__(self, engine: Engine): self.engine = engine; self.refresh()

    @property
    def db_type(self) -> str: return normalize_db_type(self.engine.dialect.name)
    def refresh(self): self._inspector = inspect(self.engine) # Cache buster

    def _quote(self, name: str) -> str:
        return self.engine.dialect.identifier_preparer.quote(name)

    def _qualified_table(self, table: str, schema: Optional[str]) -> str:
        pre = f"{self._quote(schema)}." if schema else ""
        return f"{pre}{self._quote(table)}"

    @staticmethod
    def normalize_identifier(name: str) -> str:
        if name is None:
            return ""
        cleaned = re.sub(r"[\"`\[\]]", "", str(name))
        return cleaned.strip().lower()

    # --- Inspection & Exploration ---
    @log_call
    def list_schemas(self) -> List[str]:
        try:
            return self._inspector.get_schema_names()
        except Exception as exc:
            logger.warning(f"Schema listing failed: {exc}")
            return []

    @log_call
    def list_views(self, schema: str=None, pattern: str=None) -> List[str]:
        v = self._inspector.get_view_names(schema=schema)
        return [x for x in v if re.search(pattern, x, re.I)] if pattern else v

    @log_call
    def list_tables(self, schema: str=None, pattern: str=None, include_views: bool=False) -> List[str]:
        t = list(self._inspector.get_table_names(schema=schema))
        if include_views:
            t.extend(self._inspector.get_view_names(schema=schema))
        if pattern:
            return [x for x in t if re.search(pattern, x, re.I)]
        return list(dict.fromkeys(t))

    @log_call
    def describe_schema(self, schema: str=None, pattern: str=None) -> Dict[str, Any]:
        details: Dict[str, Any] = {}
        for table in self.list_tables(schema=schema, pattern=pattern):
            details[table] = self.get_table_details(table, schema=schema)
        log_json("schema_manager.describe_schema", {
            "schema": schema,
            "table_count": len(details)
        })
        return details

    @log_call
    def find_column(self, col_pattern: str, schema: str=None) -> Dict[str, List[str]]:
        """Find tables containing columns matching pattern."""
        res, pat = {}, re.compile(col_pattern, re.I)
        for t in self.list_tables(schema):
            matches = [c['name'] for c in self._inspector.get_columns(t, schema=schema) if pat.search(c['name'])]
            if matches: res[t] = matches
        return res

    @log_call
    def get_table_details(self, table: str, schema: str=None) -> Dict[str, Any]:
        """Deep inspection of table structure."""
        insp = self._inspector
        details = {
            'columns': {c['name']: {k:v for k,v in c.items() if k!='name'} for c in insp.get_columns(table, schema=schema)},
            'pk': insp.get_pk_constraint(table, schema=schema).get('constrained_columns', []),
            'fk': insp.get_foreign_keys(table, schema=schema),
            'indexes': insp.get_indexes(table, schema=schema),
            'constraints': insp.get_unique_constraints(table, schema=schema),
            'identity': self.get_identity_columns(table, schema)
        }
        log_json("schema_manager.table_details", {
            "table": table,
            "schema": schema,
            "columns": list(details['columns'].keys())
        })
        return details

    @log_call
    def resolve_table(self, name_like: str, schema: str=None) -> Optional[Tuple[Optional[str], str]]:
        if not name_like:
            return None
        norm_target = self.normalize_identifier(name_like)
        schemas = [schema] if schema else (self.list_schemas() or [None])
        matches: List[Tuple[Optional[str], str]] = []
        for sch in schemas:
            for table in self.list_tables(schema=sch):
                norm_table = self.normalize_identifier(table)
                if norm_table == norm_target or (norm_target and norm_target in norm_table):
                    matches.append((sch, table))
                    continue
                try:
                    if re.search(name_like, table, re.I):
                        matches.append((sch, table))
                except re.error:
                    continue
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(f"Multiple table matches for '{name_like}': {matches}")
        return matches[0]

    @log_call
    def resolve_column(self, table: str, col_like: str, schema: str=None) -> Optional[str]:
        if not col_like:
            return None
        cols = [c['name'] for c in self._inspector.get_columns(table, schema=schema)]
        norm_target = self.normalize_identifier(col_like)
        mapping = {self.normalize_identifier(c): c for c in cols}
        if norm_target in mapping:
            return mapping[norm_target]
        for norm, orig in mapping.items():
            if norm_target and norm_target in norm:
                return orig
        try:
            for c in cols:
                if re.search(col_like, c, re.I):
                    return c
        except re.error:
            return None
        return None

    # --- Identity & Constraints ---
    @log_call
    def get_identity_columns(self, table: str, schema: str=None) -> Set[str]:
        is_oracle = self.db_type == 'oracle'
        try: cols = self._inspector.get_columns(table, schema=schema)
        except Exception: return set()
        return {c['name'] for c in cols if c.get('autoincrement', False) or (is_oracle and 'identity' in str(c.get('default', '') or '').lower())}

    @log_call
    def detect_timestamp_columns(self, table: str, schema: str=None) -> List[str]:
        candidates: List[Tuple[int, str]] = []
        for col in self._inspector.get_columns(table, schema=schema):
            name = col['name']
            name_l = name.lower()
            type_str = str(col.get('type', '')).lower()
            score = 0
            if any(tag in name_l for tag in self.HIGH_PRIORITY_TIMESTAMP):
                score += 100
            for pattern in self.TIMESTAMP_PATTERNS:
                if pattern in name_l:
                    score += 10
            if 'date' in type_str or 'time' in type_str:
                score += 5
            if score > 0:
                candidates.append((score, name))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [c[1] for c in candidates]

    @log_call
    def validate_upsert_constraints(self, table: str, key_cols: List[str], schema: str=None) -> None:
        if self.db_type not in ('postgres', 'mysql', 'sqlite'): return
        pk_cols = set(self._inspector.get_pk_constraint(table, schema=schema).get('constrained_columns', []))
        unique_cons = [set(uc.get('column_names', [])) for uc in self._inspector.get_unique_constraints(table, schema=schema)]
        if not (set(key_cols) <= pk_cols or any(set(key_cols) <= uc for uc in unique_cons)):
            raise ValueError(f"Upsert safety failed: {key_cols} not covered by PK/Unique on '{table}'")

    @log_call
    def table_activity_status(self, table: str, schema: str=None, max_age_days: int=30) -> Dict[str, Any]:
        ts_cols = self.detect_timestamp_columns(table, schema=schema)
        if not ts_cols:
            return {
                "table": table,
                "schema": schema,
                "status": "unknown",
                "reason": "no_timestamp_column"
            }
        col = ts_cols[0]
        qualified = self._qualified_table(table, schema)
        sql = f"SELECT MAX({self._quote(col)}) AS max_value, COUNT(*) AS row_count FROM {qualified}"
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            row = result.fetchone()
        max_value = row[0] if row else None
        row_count = int(row[1]) if row and row[1] is not None else 0
        status = "unknown"
        age_days = None
        if isinstance(max_value, datetime):
            now = datetime.now(max_value.tzinfo) if max_value.tzinfo else datetime.now()
            age_days = (now - max_value).days
            status = "active" if age_days <= max_age_days else "stale"
        elif isinstance(max_value, date):
            now = datetime.now().date()
            age_days = (now - max_value).days
            status = "active" if age_days <= max_age_days else "stale"
        elif max_value is None:
            status = "empty"
        log_json("schema_manager.table_activity", {
            "table": table,
            "schema": schema,
            "timestamp_column": col,
            "max_value": str(max_value),
            "row_count": row_count,
            "status": status,
            "age_days": age_days
        })
        return {
            "table": table,
            "schema": schema,
            "timestamp_column": col,
            "max_value": max_value,
            "row_count": row_count,
            "status": status,
            "age_days": age_days
        }

    @log_call
    def classify_table_activity(self, tables: Iterable[str], schema: str=None, max_age_days: int=30) -> Dict[str, str]:
        results: Dict[str, str] = {}
        for table in tables:
            info = self.table_activity_status(table, schema=schema, max_age_days=max_age_days)
            results[table] = info.get("status", "unknown")
        return results

    # --- Diff & Modification ---
    @log_call
    def compare_to_structure(self, table: str, structure: Dict[str, str], schema: str=None) -> Dict[str, Any]:
        """Compare table columns against {col_name: type_str} expectation."""
        curr = {c['name']: str(c['type']) for c in self._inspector.get_columns(table, schema=schema)}
        return {
            'missing_in_db': [k for k in structure if k not in curr],
            'extra_in_db': [k for k in curr if k not in structure],
            'type_mismatch': {k: f"{curr[k]} != {structure[k]}" for k in structure if k in curr and str(structure[k]).lower() not in str(curr[k]).lower()}
        }

    @log_call
    def has_table(self, table: str, schema: Optional[str]=None) -> bool:
        with self.engine.connect() as conn: return self.engine.dialect.has_table(conn, table, schema=schema)

    @log_call
    def tail(self, table: str, schema: str=None, order_by: Optional[str]=None, limit: int=5) -> List[Dict[str, Any]]:
        order_col = order_by
        if not order_col:
            pk = self._inspector.get_pk_constraint(table, schema=schema).get('constrained_columns', [])
            if pk:
                order_col = pk[0]
            else:
                ts_cols = self.detect_timestamp_columns(table, schema=schema)
                order_col = ts_cols[0] if ts_cols else None
        order_clause = f"ORDER BY {self._quote(order_col)} DESC" if order_col else ""
        qualified = self._qualified_table(table, schema)
        db = self.db_type
        if db == 'mssql':
            sql = f"SELECT TOP {limit} * FROM {qualified} {order_clause}"
        elif db == 'oracle':
            sql = f"SELECT * FROM {qualified} {order_clause} FETCH FIRST {limit} ROWS ONLY"
        else:
            sql = f"SELECT * FROM {qualified} {order_clause} LIMIT {limit}"
        log_string("schema_manager.tail_sql", sql)
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            try:
                rows = result.mappings().all()
            except AttributeError:
                rows = [dict(r) for r in result.fetchall()]
        return [dict(r) for r in rows]

    @log_call
    def add_column(self, table: str, col_name: str, col_type: str, schema: str=None) -> None:
        q = self.engine.dialect.identifier_preparer.quote
        pre = f"{q(schema)}." if schema else ""
        fmt = "({0} {1})" if self.db_type == 'oracle' else "{0} {1}"
        self._exec_ddl(f"ALTER TABLE {pre}{q(table)} ADD {fmt.format(q(col_name), col_type)}", f"Added column {col_name} to {table}")

    @log_call
    def alter_column_type(self, table: str, col_name: str, new_type: str, schema: str=None) -> None:
        q = self.engine.dialect.identifier_preparer.quote
        pre = f"{q(schema)}." if schema else ""
        db, tq, cq = self.db_type, q(table), q(col_name)
        if db == 'postgres': sql = f"ALTER TABLE {pre}{tq} ALTER COLUMN {cq} TYPE {new_type}"
        elif db == 'mysql': sql = f"ALTER TABLE {pre}{tq} MODIFY COLUMN {cq} {new_type}"
        elif db == 'oracle': sql = f"ALTER TABLE {pre}{tq} MODIFY ({cq} {new_type})"
        elif db == 'mssql': sql = f"ALTER TABLE {pre}{tq} ALTER COLUMN {cq} {new_type}"
        else: sql = f"ALTER TABLE {pre}{tq} MODIFY {cq} {new_type}"
        self._exec_ddl(sql, f"Altered column {col_name} to {new_type} on {table}")

    @log_call
    def rename_table(self, old_name: str, new_name: str, schema: str=None) -> None:
        pre = f"{self._quote(schema)}." if schema else ""
        db = self.db_type
        if db == 'mssql':
            sql = f"EXEC sp_rename '{pre}{old_name}', '{new_name}'"
        else:
            sql = f"ALTER TABLE {pre}{self._quote(old_name)} RENAME TO {self._quote(new_name)}"
        self._exec_ddl(sql, f"Renamed table {old_name} to {new_name}")

    @log_call
    def rename_column(self, table: str, old_name: str, new_name: str, schema: str=None) -> None:
        pre = f"{self._quote(schema)}." if schema else ""
        db = self.db_type
        if db == 'mssql':
            sql = f"EXEC sp_rename '{pre}{table}.{old_name}', '{new_name}', 'COLUMN'"
        else:
            sql = f"ALTER TABLE {pre}{self._quote(table)} RENAME COLUMN {self._quote(old_name)} TO {self._quote(new_name)}"
        self._exec_ddl(sql, f"Renamed column {old_name} to {new_name} on {table}")

    @log_call
    def drop_column(self, table: str, col_name: str, schema: str=None) -> None:
        pre = f"{self._quote(schema)}." if schema else ""
        sql = f"ALTER TABLE {pre}{self._quote(table)} DROP COLUMN {self._quote(col_name)}"
        self._exec_ddl(sql, f"Dropped column {col_name} on {table}")

    @log_call
    def create_index(self, table: str, columns: List[str], name: Optional[str]=None, schema: str=None, unique: bool=False) -> str:
        idx_name = name or f"idx_{table}_{'_'.join(columns)}"
        pre = f"{self._quote(schema)}." if schema else ""
        cols = ", ".join(self._quote(c) for c in columns)
        unique_sql = "UNIQUE " if unique else ""
        sql = f"CREATE {unique_sql}INDEX {self._quote(idx_name)} ON {pre}{self._quote(table)} ({cols})"
        self._exec_ddl(sql, f"Created index {idx_name} on {table}")
        return idx_name

    @log_call
    def drop_index(self, index_name: str, table: Optional[str]=None, schema: str=None, if_exists: bool=True) -> None:
        db = self.db_type
        pre = f"{self._quote(schema)}." if schema else ""
        if db in ('mysql',):
            if not table:
                raise ValueError("table is required to drop index on mysql")
            sql = f"DROP INDEX {self._quote(index_name)} ON {pre}{self._quote(table)}"
        elif db in ('mssql',):
            if not table:
                raise ValueError("table is required to drop index on mssql")
            sql = f"DROP INDEX {self._quote(index_name)} ON {pre}{self._quote(table)}"
        else:
            exists = "IF EXISTS " if if_exists and db in ('postgres', 'sqlite') else ""
            sql = f"DROP INDEX {exists}{pre}{self._quote(index_name)}"
        self._exec_ddl(sql, f"Dropped index {index_name}")

    @log_call
    def clone_table(self, source: str, target: str, schema: str=None, target_schema: Optional[str]=None, with_data: bool=True) -> None:
        db = self.db_type
        src_q = self._qualified_table(source, schema)
        dst_q = self._qualified_table(target, target_schema or schema)
        if db == 'mysql' and not with_data:
            sql = f"CREATE TABLE {dst_q} LIKE {src_q}"
        elif db == 'mssql':
            where = "" if with_data else " WHERE 1=0"
            sql = f"SELECT * INTO {dst_q} FROM {src_q}{where}"
        else:
            where = "" if with_data else " WHERE 1=0"
            sql = f"CREATE TABLE {dst_q} AS SELECT * FROM {src_q}{where}"
        self._exec_ddl(sql, f"Cloned table {source} to {target}")

    @log_call
    def copy_rows(self, source: str, target: str, schema: str=None, target_schema: Optional[str]=None, where: Optional[str]=None) -> None:
        src_q = self._qualified_table(source, schema)
        dst_q = self._qualified_table(target, target_schema or schema)
        where_clause = f" WHERE {where}" if where else ""
        sql = f"INSERT INTO {dst_q} SELECT * FROM {src_q}{where_clause}"
        self._exec_ddl(sql, f"Copied rows from {source} to {target}")

    def _exec_ddl(self, sql: str, msg: str):
        try:
            log_string("schema_manager.ddl", sql)
            log_json("schema_manager.ddl_context", {"message": msg, "db_type": self.db_type})
            with self.engine.begin() as conn: conn.execute(text(sql))
            logger.info(msg)
        except SQLAlchemyError as e:
            logger.error(f"DDL failed: {e}"); raise
