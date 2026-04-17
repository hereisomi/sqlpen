'''engine_manager.py'''
from __future__ import annotations
import atexit
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import contextmanager
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Result, CursorResult
from sqlalchemy.orm import sessionmaker, Session

# ---------- pooled engines ------------------------------------------------- #
class _Registry:
    _lock: threading.Lock = threading.Lock()
    _eng: Dict[Tuple[str, Tuple[Tuple[str, Any], ...]], Engine] = {}

    @classmethod
    def _k(cls, url: str, **opt: Any) -> Tuple[str, Tuple[Tuple[str, Any], ...]]:
        return url, tuple(sorted(opt.items()))

    @classmethod
    def get(cls, url: str, **opt: Any) -> Engine:
        k = cls._k(url, **opt)
        # FIX: Acquire lock for ALL reads to prevent race conditions
        with cls._lock:
            e = cls._eng.get(k)
            if e is None:
                base = dict(future=True, pool_pre_ping=True)
                base.update(opt)
                e = create_engine(url, **base)
                cls._eng[k] = e
            return e

    @classmethod
    def dispose(cls, url: str | None = None) -> None:
        with cls._lock:
            items = list(cls._eng.items()) if url is None else [(k, e) for k, e in cls._eng.items() if k[0] == url]
            for k, e in items:
                e.dispose()
                cls._eng.pop(k, None)

# ---------- audit / rolling text file ------------------------------------- #
class _Audit:
    _lock: threading.Lock = threading.Lock()
    _dir: str = os.getcwd()
    _fp: Any = None
    _hr: str = ''
    _mem: List[Dict[str, Any]] = []
    _registered_atexit: bool = False

    @classmethod
    def set_dir(cls, d: str) -> None:
        os.makedirs(d, exist_ok=True)
        cls._dir = d

    @classmethod
    def _ensure_atexit(cls) -> None:
        """FIX: Register atexit handler to close file handle on program exit."""
        if not cls._registered_atexit:
            atexit.register(cls._close)
            cls._registered_atexit = True

    @classmethod
    def _close(cls) -> None:
        """Close the file handle if open."""
        with cls._lock:
            if cls._fp:
                try:
                    cls._fp.flush()
                    cls._fp.close()
                except Exception:
                    pass
                cls._fp = None

    @classmethod
    def _rotate(cls) -> None:
        h = datetime.utcnow().strftime('%Y%m%d_%H')
        if h != cls._hr or cls._fp is None:
            if cls._fp:
                try:
                    cls._fp.close()
                except Exception:
                    pass
            cls._hr = h
            os.makedirs(cls._dir, exist_ok=True)
            cls._fp = open(os.path.join(cls._dir, f'audit_{h}.log'), 'a', encoding='utf8')
            cls._ensure_atexit()

    @classmethod
    def write(cls, rec: Dict[str, Any]) -> None:
        # FIX: Wrap file I/O in try/except to prevent audit failures from crashing callers
        try:
            cls._rotate()
            cls._fp.write(f"{rec['ts']}|{rec['stat']}|{rec['qry']}|{rec.get('rows','')}\n")
            cls._fp.flush()
        except Exception:
            # Audit failure should not crash the caller; silently continue
            pass
        cls._mem.append(rec)

    @classmethod
    def get_log(cls) -> List[Dict[str, Any]]:
        """FIX: Thread-safe read of audit memory."""
        with cls._lock:
            return list(cls._mem)

# ---------- buffered result for safe return -------------------------------- #
class _BufferedResult:
    """A result wrapper that holds pre-fetched rows.
    
    This allows returning results from exec() after the connection is closed,
    preventing ResourceClosedError on iteration.
    """
    def __init__(self, rows: List[Any], keys: Any, rowcount: int):
        self._rows = rows
        self._keys = list(keys) if keys else []
        self._rowcount = rowcount
        self._index = 0
    
    @property
    def rowcount(self) -> int:
        return self._rowcount
    
    @property
    def returns_rows(self) -> bool:
        return len(self._rows) > 0
    
    def keys(self) -> List[str]:
        return self._keys
    
    def fetchall(self) -> List[Any]:
        return self._rows
    
    def fetchone(self) -> Any:
        if self._index < len(self._rows):
            row = self._rows[self._index]
            self._index += 1
            return row
        return None
    
    def fetchmany(self, size: int = 1) -> List[Any]:
        result = self._rows[self._index:self._index + size]
        self._index += size
        return result
    
    def __iter__(self):
        return iter(self._rows)
    
    def __len__(self):
        return len(self._rows)
    
    def all(self) -> List[Any]:
        return self._rows
    
    def first(self) -> Any:
        return self._rows[0] if self._rows else None
    
    def one(self) -> Any:
        if len(self._rows) != 1:
            raise ValueError(f"Expected exactly one row, got {len(self._rows)}")
        return self._rows[0]
    
    def one_or_none(self) -> Any:
        if len(self._rows) > 1:
            raise ValueError(f"Expected at most one row, got {len(self._rows)}")
        return self._rows[0] if self._rows else None
    
    def scalar(self) -> Any:
        row = self.first()
        return row[0] if row else None
    
    def scalar_one(self) -> Any:
        row = self.one()
        return row[0]
    
    def scalar_one_or_none(self) -> Any:
        row = self.one_or_none()
        return row[0] if row else None


# ---------- decorator ------------------------------------------------------ #

def _audit(fn):
    def wrap(self, *a, **kw):
        ts = datetime.utcnow().isoformat()
        qry = str(a[0] if a else kw.get('query', ''))[:240]
        prm = kw.get('params') or {}
        stat = "ok"
        rows = ""
        try:
            res = fn(self, *a, **kw)
            rows = getattr(res, 'rowcount', '')
            return res
        except Exception as ex:
            stat = f'err:{ex}'
            raise
        finally:
            with _Audit._lock:
                _Audit.write(dict(ts=ts, stat=stat, qry=qry, params=prm, rows=rows))
    return wrap

# ========================= public manager ================================= #
class EngineManager:
    def __init__(self, url: str, **eng_opt: Any):
        self._url = url
        self._eng_opt = eng_opt

    # ---- factory --------------------------------------------------------- #
    @classmethod
    def init(cls, url: str, log_dir: str | None = None, **eng_opt: Any) -> 'EngineManager':
        if log_dir:
            _Audit.set_dir(log_dir)
        return cls(url, **eng_opt)

    # ---- resources ------------------------------------------------------- #
    @property
    def engine(self) -> Engine:
        return _Registry.get(self._url, **self._eng_opt)

    @property
    def get_engin(self) -> Engine:       # alias kept for user’s earlier typo
        return self.engine

    def session(self) -> Session:
        return sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False)()

    @contextmanager
    def session_scope(self):
        s = self.session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---- execution ------------------------------------------------------- #
    @_audit
    def exec(self, query: Any, params: Dict[str, Any] | None = None) -> CursorResult:
        """Execute a query and return a buffered result.
        
        FIX: Fetch all results before connection closes to prevent ResourceClosedError.
        For SELECT queries, results are buffered. For INSERT/UPDATE/DELETE, rowcount is preserved.
        """
        q = text(query) if isinstance(query, str) else query
        with self.engine.begin() as conn:
            result = conn.execute(q, params or {})
            # Buffer results before connection closes
            # This creates a new CursorResult with all rows fetched
            if result.returns_rows:
                # Fetch all rows to buffer them
                rows = result.fetchall()
                # Return a mappings-compatible structure
                return _BufferedResult(rows, result.keys(), result.rowcount)
            else:
                # For non-SELECT statements, keys() is not available
                # Just preserve rowcount with empty rows/keys
                return _BufferedResult([], [], result.rowcount)

    # ---- exploration ----------------------------------------------------- #
    def tables(self) -> List[str]:
        return inspect(self.engine).get_table_names()

    def has_table(self, table: str) -> bool:
        """Check if table exists (case-insensitive). Returns True/False."""
        tbl = inspect(self.engine).get_table_names()
        return table in tbl or table.upper() in tbl or table.lower() in tbl

    def resolve_table_name(self, table: str) -> Optional[str]:
        """Resolve table name with correct casing. Returns actual name or None."""
        tbl = inspect(self.engine).get_table_names()
        if table in tbl:
            return table
        elif table.upper() in tbl:
            return table.upper()
        elif table.lower() in tbl:
            return table.lower()
        return None

    def table_schema(self, table: str) -> Dict[str, str]:
        cols = inspect(self.engine).get_columns(table)
        return {c['name']: str(c['type']) for c in cols}
    
    def table_columns_dtypes(self, table_name: str) -> Dict[str, str]:
        """Get column names and their type names.
        
        FIX: Use str(type) as fallback since __visit_name__ is not public API.
        """
        columns_info = inspect(self.engine).get_columns(table_name)
        result = {}
        for col in columns_info:
            col_type = col.get("type")
            if col_type is not None:
                # Try __visit_name__ first, fall back to str representation
                type_name = getattr(col_type, '__visit_name__', None) or str(col_type)
            else:
                type_name = 'UNKNOWN'
            result[col["name"]] = type_name
        return result
    
    def table_columns(self, table_name): return inspect(self.engine).get_columns(table_name)
    def table_pk(self, table_name): return inspect(self.engine).get_pk_constraint(table_name)
    def table_fk(self, table_name): return inspect(self.engine).get_foreign_keys(table_name)
    def table_indexs(self, table_name): return inspect(self.engine).get_indexes(table_name)
    def table_constraints(self, table_name): return inspect(self.engine).get_unique_constraints(table_name)

    # ---- cleanup / audit view ------------------------------------------- #
    def dispose(self) -> None:
        _Registry.dispose(self._url)

    @staticmethod
    def dispose_all() -> None:
        _Registry.dispose()

    @staticmethod
    def audit_log() -> List[Dict[str, Any]]:
        # FIX: Use thread-safe accessor
        return _Audit.get_log()
            


# ------------------------------ usage ------------------------------------- #
if __name__ == '__main__':
    log_dir = 'logs'
    URL = 'sqlite+pysqlite:///ex.db'                                  # change to pg url in prod
    db_mgr = EngineManager.init(URL, log_dir=log_dir)
    db_mgr.exec('CREATE TABLE IF NOT EXISTS users(id INTEGER, name TEXT, age INTEGER)')

    def insert(engin: Engine, data: Dict[str, Any], table: str) -> None:
        # FIX: Validate table/column names to prevent SQL injection
        # In production, use SQLAlchemy Table objects or proper quoting
        import re
        identifier_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
        if not identifier_pattern.match(table):
            raise ValueError(f"Invalid table name: {table}")
        for col in data.keys():
            if not identifier_pattern.match(col):
                raise ValueError(f"Invalid column name: {col}")
        cols = ','.join(data)
        binds = ','.join(f':{k}' for k in data)
        with engin.begin() as conn:
            conn.execute(text(f'INSERT INTO {table}({cols}) VALUES({binds})'), data)

    insert(db_mgr.engine, dict(id=1, name='Tom', age=33), 'users')
    with db_mgr.engine.connect() as conn:
        print(conn.execute(text('SELECT * FROM users')).all())             # [(1, 'Tom', 33)]

    print('tables:', db_mgr.tables())                                      # ['users']
    print('audit in memory:', EngineManager.audit_log()[:2])               # quick peek
    db_mgr.dispose()
