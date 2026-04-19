"""
Type system for DataFrame-to-SQL alignment.

Provides canonical type families and dialect-specific mappings,
with Oracle-first semantics.
"""

from enum import Enum
from typing import Dict, Any, Optional, Tuple
import sqlalchemy as sa
from sqlalchemy.dialects import oracle, postgresql, mssql, mysql, sqlite


class TypeFamily(Enum):
    """Canonical type families across all supported databases."""
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    STRING = "string"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    DATE = "date"
    TIME = "time"
    BINARY = "binary"
    JSON = "json"
    TEXT = "text"
    UNKNOWN = "unknown"


# Oracle-first type mappings
ORACLE_TYPE_MAPPINGS = {
    # Numeric types
    "NUMBER": TypeFamily.DECIMAL,
    "BINARY_FLOAT": TypeFamily.FLOAT,
    "BINARY_DOUBLE": TypeFamily.FLOAT,
    "INTEGER": TypeFamily.INTEGER,
    "SMALLINT": TypeFamily.INTEGER,
    "NUMERIC": TypeFamily.DECIMAL,
    
    # String types
    "VARCHAR2": TypeFamily.STRING,
    "NVARCHAR2": TypeFamily.STRING,
    "VARCHAR": TypeFamily.STRING,
    "CHAR": TypeFamily.STRING,
    "NCHAR": TypeFamily.STRING,
    "CLOB": TypeFamily.TEXT,
    "NCLOB": TypeFamily.TEXT,
    "LONG": TypeFamily.TEXT,
    
    # Date/Time types
    "DATE": TypeFamily.DATETIME,  # Oracle DATE includes time
    "TIMESTAMP": TypeFamily.DATETIME,
    "TIMESTAMP WITH TIME ZONE": TypeFamily.DATETIME,
    "TIMESTAMP WITH LOCAL TIME ZONE": TypeFamily.DATETIME,
    "INTERVAL YEAR TO MONTH": TypeFamily.STRING,  # Store as string
    "INTERVAL DAY TO SECOND": TypeFamily.STRING,  # Store as string
    
    # Boolean (emulated)
    "CHAR(1)": TypeFamily.BOOLEAN,  # 'Y'/'N' convention
    
    # Binary types
    "BLOB": TypeFamily.BINARY,
    "RAW": TypeFamily.BINARY,
    "LONG RAW": TypeFamily.BINARY,
    
    # JSON (Oracle 12c+)
    "JSON": TypeFamily.JSON,
    "CLOB": TypeFamily.JSON,  # Can store JSON as CLOB
}


# PostgreSQL type mappings
POSTGRES_TYPE_MAPPINGS = {
    # Numeric
    "integer": TypeFamily.INTEGER,
    "smallint": TypeFamily.INTEGER,
    "bigint": TypeFamily.INTEGER,
    "numeric": TypeFamily.DECIMAL,
    "decimal": TypeFamily.DECIMAL,
    "real": TypeFamily.FLOAT,
    "double precision": TypeFamily.FLOAT,
    "smallserial": TypeFamily.INTEGER,
    "serial": TypeFamily.INTEGER,
    "bigserial": TypeFamily.INTEGER,
    "money": TypeFamily.DECIMAL,
    
    # String
    "character varying": TypeFamily.STRING,
    "varchar": TypeFamily.STRING,
    "character": TypeFamily.STRING,
    "char": TypeFamily.STRING,
    "text": TypeFamily.TEXT,
    
    # Date/Time
    "timestamp": TypeFamily.DATETIME,
    "timestamp with time zone": TypeFamily.DATETIME,
    "timestamp without time zone": TypeFamily.DATETIME,
    "date": TypeFamily.DATE,
    "time": TypeFamily.TIME,
    "time with time zone": TypeFamily.TIME,
    "time without time zone": TypeFamily.TIME,
    "interval": TypeFamily.STRING,
    
    # Boolean
    "boolean": TypeFamily.BOOLEAN,
    "bool": TypeFamily.BOOLEAN,
    
    # Binary
    "bytea": TypeFamily.BINARY,
    
    # JSON
    "json": TypeFamily.JSON,
    "jsonb": TypeFamily.JSON,
}


# SQL Server type mappings
MSSQL_TYPE_MAPPINGS = {
    # Numeric
    "int": TypeFamily.INTEGER,
    "bigint": TypeFamily.INTEGER,
    "smallint": TypeFamily.INTEGER,
    "tinyint": TypeFamily.INTEGER,
    "bit": TypeFamily.BOOLEAN,
    "decimal": TypeFamily.DECIMAL,
    "numeric": TypeFamily.DECIMAL,
    "float": TypeFamily.FLOAT,
    "real": TypeFamily.FLOAT,
    "money": TypeFamily.DECIMAL,
    "smallmoney": TypeFamily.DECIMAL,
    
    # String
    "char": TypeFamily.STRING,
    "varchar": TypeFamily.STRING,
    "text": TypeFamily.TEXT,
    "nchar": TypeFamily.STRING,
    "nvarchar": TypeFamily.STRING,
    "ntext": TypeFamily.TEXT,
    
    # Date/Time
    "date": TypeFamily.DATE,
    "time": TypeFamily.TIME,
    "datetime": TypeFamily.DATETIME,
    "datetime2": TypeFamily.DATETIME,
    "datetimeoffset": TypeFamily.DATETIME,
    "smalldatetime": TypeFamily.DATETIME,
    
    # Binary
    "binary": TypeFamily.BINARY,
    "varbinary": TypeFamily.BINARY,
    "image": TypeFamily.BINARY,
    
    # JSON (SQL Server 2016+)
    "nvarchar": TypeFamily.JSON,  # JSON stored as NVARCHAR
}


# MySQL/MariaDB type mappings
MYSQL_TYPE_MAPPINGS = {
    # Numeric
    "tinyint": TypeFamily.INTEGER,
    "smallint": TypeFamily.INTEGER,
    "mediumint": TypeFamily.INTEGER,
    "int": TypeFamily.INTEGER,
    "integer": TypeFamily.INTEGER,
    "bigint": TypeFamily.INTEGER,
    "decimal": TypeFamily.DECIMAL,
    "dec": TypeFamily.DECIMAL,
    "numeric": TypeFamily.DECIMAL,
    "float": TypeFamily.FLOAT,
    "double": TypeFamily.FLOAT,
    "double precision": TypeFamily.FLOAT,
    "real": TypeFamily.FLOAT,
    "bit": TypeFamily.BOOLEAN,
    "boolean": TypeFamily.BOOLEAN,
    "serial": TypeFamily.INTEGER,
    
    # String
    "char": TypeFamily.STRING,
    "varchar": TypeFamily.STRING,
    "binary": TypeFamily.BINARY,
    "varbinary": TypeFamily.BINARY,
    "tinyblob": TypeFamily.BINARY,
    "blob": TypeFamily.BINARY,
    "mediumblob": TypeFamily.BINARY,
    "longblob": TypeFamily.BINARY,
    "tinytext": TypeFamily.TEXT,
    "text": TypeFamily.TEXT,
    "mediumtext": TypeFamily.TEXT,
    "longtext": TypeFamily.TEXT,
    "enum": TypeFamily.STRING,
    "set": TypeFamily.STRING,
    
    # Date/Time
    "date": TypeFamily.DATE,
    "time": TypeFamily.TIME,
    "datetime": TypeFamily.DATETIME,
    "timestamp": TypeFamily.DATETIME,
    "year": TypeFamily.INTEGER,
    
    # JSON (MySQL 5.7+)
    "json": TypeFamily.JSON,
}


# SQLite type mappings (affinity-based)
SQLITE_TYPE_MAPPINGS = {
    "INTEGER": TypeFamily.INTEGER,
    "TEXT": TypeFamily.TEXT,
    "REAL": TypeFamily.FLOAT,
    "NUMERIC": TypeFamily.DECIMAL,
    "BLOB": TypeFamily.BINARY,
}


# Dialect-specific mapping registry
DIALECT_MAPPINGS = {
    "oracle": ORACLE_TYPE_MAPPINGS,
    "postgresql": POSTGRES_TYPE_MAPPINGS,
    "mssql": MSSQL_TYPE_MAPPINGS,
    "mysql": MYSQL_TYPE_MAPPINGS,
    "sqlite": SQLITE_TYPE_MAPPINGS,
}


def infer_type_family(sa_type: sa.types.TypeEngine, dialect: str) -> TypeFamily:
    """
    Infer the canonical type family from a SQLAlchemy type and dialect.
    
    Args:
        sa_type: SQLAlchemy type object
        dialect: Database dialect name
        
    Returns:
        TypeFamily: Canonical type family
    """
    # Get dialect-specific mappings
    mappings = DIALECT_MAPPINGS.get(dialect.lower(), ORACLE_TYPE_MAPPINGS)
    
    # Get the type name as it would appear in the database
    type_name = str(sa_type).upper()
    
    # Handle Oracle-specific cases
    if dialect.lower() == "oracle":
        # Handle VARCHAR2 with semantics
        if type_name.startswith("VARCHAR2"):
            return TypeFamily.STRING
        # Handle NUMBER with precision/scale
        elif type_name.startswith("NUMBER"):
            return TypeFamily.DECIMAL
        # Handle TIMESTAMP variants
        elif "TIMESTAMP" in type_name:
            return TypeFamily.DATETIME
    
    # Direct lookup in mappings
    family = mappings.get(type_name, TypeFamily.UNKNOWN)
    
    # Fallback to generic type detection
    if family == TypeFamily.UNKNOWN:
        if isinstance(sa_type, (sa.Integer, sa.SmallInteger, sa.BigInteger)):
            family = TypeFamily.INTEGER
        elif isinstance(sa_type, sa.Float):
            family = TypeFamily.FLOAT
        elif isinstance(sa_type, (sa.Numeric, sa.DECIMAL)):
            family = TypeFamily.DECIMAL
        elif isinstance(sa_type, (sa.String, sa.Unicode, sa.UnicodeText, sa.Text)):
            family = TypeFamily.STRING
        elif isinstance(sa_type, sa.Boolean):
            family = TypeFamily.BOOLEAN
        elif isinstance(sa_type, (sa.DateTime, sa.TIMESTAMP)):
            family = TypeFamily.DATETIME
        elif isinstance(sa_type, sa.Date):
            family = TypeFamily.DATE
        elif isinstance(sa_type, sa.Time):
            family = TypeFamily.TIME
        elif isinstance(sa_type, sa.LargeBinary):
            family = TypeFamily.BINARY
        elif isinstance(sa_type, sa.JSON):
            family = TypeFamily.JSON
        else:
            family = TypeFamily.UNKNOWN
    
    return family


def extract_limits(sa_type: sa.types.TypeEngine, dialect: str) -> Dict[str, Any]:
    """
    Extract type-specific limits and attributes from SQLAlchemy type.
    
    Args:
        sa_type: SQLAlchemy type object
        dialect: Database dialect name
        
    Returns:
        Dict containing extracted limits
    """
    limits = {}
    
    # String types
    if isinstance(sa_type, (sa.String, sa.Unicode)):
        limits['max_length'] = sa_type.length
        
        # Oracle VARCHAR2 character/byte semantics
        if dialect.lower() == "oracle" and hasattr(sa_type, 'char_semantics'):
            limits['char_semantics'] = sa_type.char_semantics
    
    # Numeric types
    elif isinstance(sa_type, (sa.Numeric, sa.DECIMAL)):
        limits['precision'] = sa_type.precision
        limits['scale'] = sa_type.scale
        
        # Oracle NUMBER special handling
        if dialect.lower() == "oracle":
            if sa_type.precision is None and sa_type.scale is None:
                # Unconstrained NUMBER - treat as flexible precision
                limits['precision'] = 38  # Oracle max precision
                limits['scale'] = None
    
    # Date/Time types
    elif isinstance(sa_type, (sa.DateTime, sa.TIMESTAMP)):
        limits['timezone'] = getattr(sa_type, 'timezone', False)
        
        # Oracle TIMESTAMP variants
        if dialect.lower() == "oracle":
            if hasattr(sa_type, 'timezone') and sa_type.timezone:
                limits['timezone'] = True
    
    # Binary types
    elif isinstance(sa_type, sa.LargeBinary):
        limits['max_length'] = getattr(sa_type, 'length', None)
    
    return limits


def get_canonical_sql_type(type_family: TypeFamily, dialect: str, **limits) -> str:
    """
    Get the canonical SQL type string for a type family and dialect.
    
    Args:
        type_family: Canonical type family
        dialect: Database dialect name
        **limits: Type-specific limits (length, precision, scale, etc.)
        
    Returns:
        SQL type string appropriate for the dialect
    """
    dialect = dialect.lower()
    
    if type_family == TypeFamily.INTEGER:
        if dialect == "oracle":
            return "NUMBER(10)"
        elif dialect == "postgresql":
            return "INTEGER"
        elif dialect == "mssql":
            return "INT"
        elif dialect == "mysql":
            return "INT"
        elif dialect == "sqlite":
            return "INTEGER"
        else:
            return "INTEGER"
    
    elif type_family == TypeFamily.FLOAT:
        if dialect == "oracle":
            return "BINARY_DOUBLE"
        elif dialect == "postgresql":
            return "DOUBLE PRECISION"
        elif dialect == "mssql":
            return "FLOAT"
        elif dialect == "mysql":
            return "DOUBLE"
        elif dialect == "sqlite":
            return "REAL"
        else:
            return "FLOAT"
    
    elif type_family == TypeFamily.DECIMAL:
        precision = limits.get('precision', 38)
        scale = limits.get('scale', None)
        
        if dialect == "oracle":
            if scale is not None:
                return f"NUMBER({precision},{scale})"
            else:
                return f"NUMBER({precision})"
        elif dialect == "postgresql":
            if scale is not None:
                return f"NUMERIC({precision},{scale})"
            else:
                return f"NUMERIC({precision})"
        elif dialect == "mssql":
            if scale is not None:
                return f"DECIMAL({precision},{scale})"
            else:
                return f"DECIMAL({precision})"
        elif dialect == "mysql":
            if scale is not None:
                return f"DECIMAL({precision},{scale})"
            else:
                return f"DECIMAL({precision})"
        elif dialect == "sqlite":
            return "NUMERIC"
        else:
            if scale is not None:
                return f"DECIMAL({precision},{scale})"
            else:
                return f"DECIMAL({precision})"
    
    elif type_family == TypeFamily.STRING:
        length = limits.get('max_length', 255)
        
        if dialect == "oracle":
            char_semantics = limits.get('char_semantics', 'BYTE')
            return f"VARCHAR2({length} {char_semantics})"
        elif dialect == "postgresql":
            return f"VARCHAR({length})"
        elif dialect == "mssql":
            return f"VARCHAR({length})"
        elif dialect == "mysql":
            return f"VARCHAR({length})"
        elif dialect == "sqlite":
            return "TEXT"
        else:
            return f"VARCHAR({length})"
    
    elif type_family == TypeFamily.TEXT:
        if dialect == "oracle":
            return "CLOB"
        elif dialect == "postgresql":
            return "TEXT"
        elif dialect == "mssql":
            return "TEXT"
        elif dialect == "mysql":
            return "LONGTEXT"
        elif dialect == "sqlite":
            return "TEXT"
        else:
            return "TEXT"
    
    elif type_family == TypeFamily.BOOLEAN:
        if dialect == "oracle":
            return "CHAR(1)"  # Y/N convention
        elif dialect == "postgresql":
            return "BOOLEAN"
        elif dialect == "mssql":
            return "BIT"
        elif dialect == "mysql":
            return "BOOLEAN"
        elif dialect == "sqlite":
            return "INTEGER"  # 0/1 convention
        else:
            return "BOOLEAN"
    
    elif type_family == TypeFamily.DATETIME:
        if dialect == "oracle":
            timezone = limits.get('timezone', False)
            if timezone:
                return "TIMESTAMP WITH TIME ZONE"
            else:
                return "TIMESTAMP"
        elif dialect == "postgresql":
            timezone = limits.get('timezone', False)
            if timezone:
                return "TIMESTAMP WITH TIME ZONE"
            else:
                return "TIMESTAMP"
        elif dialect == "mssql":
            return "DATETIME2"
        elif dialect == "mysql":
            return "DATETIME"
        elif dialect == "sqlite":
            return "TEXT"  # Store as ISO string
        else:
            return "TIMESTAMP"
    
    elif type_family == TypeFamily.DATE:
        if dialect == "oracle":
            return "DATE"
        elif dialect == "postgresql":
            return "DATE"
        elif dialect == "mssql":
            return "DATE"
        elif dialect == "mysql":
            return "DATE"
        elif dialect == "sqlite":
            return "TEXT"  # Store as ISO string
        else:
            return "DATE"
    
    elif type_family == TypeFamily.TIME:
        if dialect == "oracle":
            return "TIMESTAMP"  # Oracle doesn't have pure TIME
        elif dialect == "postgresql":
            return "TIME"
        elif dialect == "mssql":
            return "TIME"
        elif dialect == "mysql":
            return "TIME"
        elif dialect == "sqlite":
            return "TEXT"  # Store as ISO string
        else:
            return "TIME"
    
    elif type_family == TypeFamily.BINARY:
        length = limits.get('max_length', None)
        
        if dialect == "oracle":
            if length:
                return f"RAW({length})"
            else:
                return "BLOB"
        elif dialect == "postgresql":
            return "BYTEA"
        elif dialect == "mssql":
            if length:
                return f"VARBINARY({length})"
            else:
                return "VARBINARY(MAX)"
        elif dialect == "mysql":
            if length:
                return f"VARBINARY({length})"
            else:
                return "LONGBLOB"
        elif dialect == "sqlite":
            return "BLOB"
        else:
            return "BINARY"
    
    elif type_family == TypeFamily.JSON:
        if dialect == "oracle":
            return "JSON"  # Oracle 12c+
        elif dialect == "postgresql":
            return "JSONB"
        elif dialect == "mssql":
            return "NVARCHAR(MAX)"  # JSON as NVARCHAR
        elif dialect == "mysql":
            return "JSON"
        elif dialect == "sqlite":
            return "TEXT"  # Store as text
        else:
            return "JSON"
    
    else:
        return "TEXT"  # Safe fallback


def is_type_compatible(
    source_family: TypeFamily, 
    target_family: TypeFamily, 
    dialect: str
) -> bool:
    """
    Check if a source type family can be safely coerced to target type family.
    
    Args:
        source_family: Source type family
        target_family: Target type family
        dialect: Database dialect
        
    Returns:
        bool: True if compatible, False otherwise
    """
    # Same type is always compatible
    if source_family == target_family:
        return True
    
    # Integer to float/decimal is generally safe
    if source_family == TypeFamily.INTEGER and target_family in (TypeFamily.FLOAT, TypeFamily.DECIMAL):
        return True
    
    # Float to decimal is generally safe
    if source_family == TypeFamily.FLOAT and target_family == TypeFamily.DECIMAL:
        return True
    
    # String to text is always safe
    if source_family == TypeFamily.STRING and target_family == TypeFamily.TEXT:
        return True
    
    # Boolean to integer is safe (0/1)
    if source_family == TypeFamily.BOOLEAN and target_family == TypeFamily.INTEGER:
        return True
    
    # String to boolean (Y/N, true/false, 1/0) - requires validation
    if source_family == TypeFamily.STRING and target_family == TypeFamily.BOOLEAN:
        return True
    
    # String to datetime - requires format validation
    if source_family == TypeFamily.STRING and target_family in (TypeFamily.DATETIME, TypeFamily.DATE, TypeFamily.TIME):
        return True
    
    # String to JSON - requires format validation
    if source_family == TypeFamily.STRING and target_family == TypeFamily.JSON:
        return True
    
    # Text to string - may require length validation
    if source_family == TypeFamily.TEXT and target_family == TypeFamily.STRING:
        return True
    
    return False
