"""
Engine information and capabilities detection for DataFrame-to-SQL alignment.

Provides database engine introspection and capability detection
to guide safe DDL operations and type mappings.
"""

import sqlalchemy as sa
from typing import Dict, Any, Optional, List
from .models import EngineInfo


def get_engine_info(engine: sa.engine.Engine) -> EngineInfo:
    """
    Get comprehensive information about the database engine.
    
    Args:
        engine: SQLAlchemy engine instance
        
    Returns:
        EngineInfo with engine details
    """
    dialect_name = engine.dialect.name
    dialect_version = getattr(engine.dialect, 'server_version_info', None)
    
    # Get server version
    server_version = None
    try:
        with engine.connect() as conn:
            if dialect_name == "oracle":
                result = conn.execute(sa.text("SELECT VERSION FROM V$INSTANCE"))
                server_version = result.scalar()
            elif dialect_name == "postgresql":
                result = conn.execute(sa.text("SELECT version()"))
                server_version = result.scalar()
            elif dialect_name == "mssql":
                result = conn.execute(sa.text("SELECT @@VERSION"))
                server_version = result.scalar()
            elif dialect_name == "mysql":
                result = conn.execute(sa.text("SELECT VERSION()"))
                server_version = result.scalar()
            elif dialect_name == "sqlite":
                result = conn.execute(sa.text("SELECT sqlite_version()"))
                server_version = result.scalar()
    except Exception:
        # Version detection failed, continue without it
        pass
    
    # Convert version info to string
    if dialect_version:
        if isinstance(dialect_version, tuple):
            version_str = ".".join(str(v) for v in dialect_version)
        else:
            version_str = str(dialect_version)
    else:
        version_str = "unknown"
    
    return EngineInfo(
        dialect=dialect_name,
        version=version_str,
        server_version=server_version,
        capabilities=get_capabilities(engine)
    )


def get_capabilities(engine: sa.engine.Engine) -> Dict[str, bool]:
    """
    Detect database engine capabilities for safe DDL operations.
    
    Args:
        engine: SQLAlchemy engine instance
        
    Returns:
        Dictionary of capability flags
    """
    dialect = engine.dialect.name.lower()
    capabilities = {}
    
    # DDL capabilities
    capabilities.update(get_ddl_capabilities(dialect))
    
    # Transaction capabilities
    capabilities.update(get_transaction_capabilities(dialect))
    
    # Type capabilities
    capabilities.update(get_type_capabilities(dialect))
    
    # Index capabilities
    capabilities.update(get_index_capabilities(dialect))
    
    # Constraint capabilities
    capabilities.update(get_constraint_capabilities(dialect))
    
    return capabilities


def get_ddl_capabilities(dialect: str) -> Dict[str, bool]:
    """
    Get DDL operation capabilities for the dialect.
    
    Args:
        dialect: Database dialect name
        
    Returns:
        DDL capability flags
    """
    capabilities = {}
    
    if dialect == "oracle":
        capabilities.update({
            'supports_add_column': True,
            'supports_drop_column': True,
            'supports_alter_column_type': True,  # Limited
            'supports_alter_column_nullability': True,
            'supports_alter_column_default': True,
            'supports_rename_column': True,
            'supports_rename_table': True,
            'supports_add_constraint': True,
            'supports_drop_constraint': True,
            'supports_online_ddl': True,  # Oracle 12c+
            'requires_transaction_for_ddl': False,  # Auto-commits
            'supports_if_not_exists': False,
            'supports_cascade': True,
        })
    elif dialect == "postgresql":
        capabilities.update({
            'supports_add_column': True,
            'supports_drop_column': True,
            'supports_alter_column_type': True,
            'supports_alter_column_nullability': True,
            'supports_alter_column_default': True,
            'supports_rename_column': True,
            'supports_rename_table': True,
            'supports_add_constraint': True,
            'supports_drop_constraint': True,
            'supports_online_ddl': True,
            'requires_transaction_for_ddl': True,
            'supports_if_not_exists': True,
            'supports_cascade': True,
        })
    elif dialect == "mssql":
        capabilities.update({
            'supports_add_column': True,
            'supports_drop_column': True,
            'supports_alter_column_type': True,  # Limited
            'supports_alter_column_nullability': True,
            'supports_alter_column_default': True,
            'supports_rename_column': True,
            'supports_rename_table': True,
            'supports_add_constraint': True,
            'supports_drop_constraint': True,
            'supports_online_ddl': True,  # Enterprise Edition
            'requires_transaction_for_ddl': True,
            'supports_if_not_exists': False,  # Limited support
            'supports_cascade': True,
        })
    elif dialect == "mysql":
        capabilities.update({
            'supports_add_column': True,
            'supports_drop_column': True,
            'supports_alter_column_type': True,
            'supports_alter_column_nullability': True,
            'supports_alter_column_default': True,
            'supports_rename_column': True,
            'supports_rename_table': True,
            'supports_add_constraint': True,
            'supports_drop_constraint': True,
            'supports_online_ddl': True,  # MySQL 5.6+
            'requires_transaction_for_ddl': False,  # Auto-commits
            'supports_if_not_exists': True,
            'supports_cascade': True,
        })
    elif dialect == "sqlite":
        capabilities.update({
            'supports_add_column': True,  # Limited
            'supports_drop_column': False,  # Not supported
            'supports_alter_column_type': False,  # Very limited
            'supports_alter_column_nullability': False,
            'supports_alter_column_default': False,
            'supports_rename_column': True,
            'supports_rename_table': True,
            'supports_add_constraint': False,  # Very limited
            'supports_drop_constraint': False,
            'supports_online_ddl': True,  # Single-user DB
            'requires_transaction_for_ddl': True,
            'supports_if_not_exists': False,
            'supports_cascade': False,
        })
    else:
        # Conservative defaults for unknown dialects
        capabilities.update({
            'supports_add_column': True,
            'supports_drop_column': False,
            'supports_alter_column_type': False,
            'supports_alter_column_nullability': False,
            'supports_alter_column_default': False,
            'supports_rename_column': False,
            'supports_rename_table': False,
            'supports_add_constraint': False,
            'supports_drop_constraint': False,
            'supports_online_ddl': False,
            'requires_transaction_for_ddl': True,
            'supports_if_not_exists': False,
            'supports_cascade': False,
        })
    
    return capabilities


def get_transaction_capabilities(dialect: str) -> Dict[str, bool]:
    """
    Get transaction handling capabilities for the dialect.
    
    Args:
        dialect: Database dialect name
        
    Returns:
        Transaction capability flags
    """
    capabilities = {}
    
    if dialect == "oracle":
        capabilities.update({
            'supports_transactions': True,
            'supports_savepoints': True,
            'supports_nested_transactions': False,
            'supports_implicit_transactions': False,
            'supports_read_committed': True,
            'supports_serializable': True,
            'supports_read_only': True,
        })
    elif dialect == "postgresql":
        capabilities.update({
            'supports_transactions': True,
            'supports_savepoints': True,
            'supports_nested_transactions': False,
            'supports_implicit_transactions': False,
            'supports_read_committed': True,
            'supports_serializable': True,
            'supports_read_only': True,
        })
    elif dialect == "mssql":
        capabilities.update({
            'supports_transactions': True,
            'supports_savepoints': True,
            'supports_nested_transactions': False,
            'supports_implicit_transactions': True,  # Can be enabled
            'supports_read_committed': True,
            'supports_serializable': True,
            'supports_read_only': True,
        })
    elif dialect == "mysql":
        capabilities.update({
            'supports_transactions': True,  # InnoDB only
            'supports_savepoints': True,
            'supports_nested_transactions': False,
            'supports_implicit_transactions': False,
            'supports_read_committed': True,
            'supports_serializable': True,
            'supports_read_only': True,
        })
    elif dialect == "sqlite":
        capabilities.update({
            'supports_transactions': True,
            'supports_savepoints': True,
            'supports_nested_transactions': False,
            'supports_implicit_transactions': True,
            'supports_read_committed': True,  # Default
            'supports_serializable': True,
            'supports_read_only': False,
        })
    else:
        # Conservative defaults
        capabilities.update({
            'supports_transactions': True,
            'supports_savepoints': False,
            'supports_nested_transactions': False,
            'supports_implicit_transactions': False,
            'supports_read_committed': False,
            'supports_serializable': False,
            'supports_read_only': False,
        })
    
    return capabilities


def get_type_capabilities(dialect: str) -> Dict[str, bool]:
    """
    Get data type capabilities for the dialect.
    
    Args:
        dialect: Database dialect name
        
    Returns:
        Type capability flags
    """
    capabilities = {}
    
    if dialect == "oracle":
        capabilities.update({
            'supports_json': True,  # Oracle 12c+
            'supports_array': False,
            'supports_uuid': False,  # Can use RAW
            'supports_boolean': False,  # Uses CHAR(1)
            'supports_enum': False,
            'supports_set': False,
            'supports_spatial': True,  # Oracle Spatial
            'supports_full_text': True,  # Oracle Text
            'supports_xml': True,
            'supports_character_semantics': True,  # VARCHAR2(CHAR)
            'supports_identity': True,  # Oracle 12c+
            'supports_sequence': True,
        })
    elif dialect == "postgresql":
        capabilities.update({
            'supports_json': True,
            'supports_array': True,
            'supports_uuid': True,
            'supports_boolean': True,
            'supports_enum': True,
            'supports_set': False,
            'supports_spatial': True,  # PostGIS
            'supports_full_text': True,
            'supports_xml': True,
            'supports_character_semantics': False,
            'supports_identity': True,
            'supports_sequence': True,
        })
    elif dialect == "mssql":
        capabilities.update({
            'supports_json': True,  # SQL Server 2016+
            'supports_array': False,
            'supports_uuid': True,  # UNIQUEIDENTIFIER
            'supports_boolean': True,  # BIT
            'supports_enum': False,
            'supports_set': False,
            'supports_spatial': True,
            'supports_full_text': True,
            'supports_xml': True,
            'supports_character_semantics': False,
            'supports_identity': True,
            'supports_sequence': True,  # SQL Server 2012+
        })
    elif dialect == "mysql":
        capabilities.update({
            'supports_json': True,  # MySQL 5.7+
            'supports_array': False,
            'supports_uuid': False,  # Can use BINARY(16)
            'supports_boolean': True,  # MySQL 5.0+
            'supports_enum': True,
            'supports_set': True,
            'supports_spatial': True,
            'supports_full_text': True,
            'supports_xml': False,
            'supports_character_semantics': False,
            'supports_identity': True,
            'supports_sequence': False,  # MySQL 8.0+
        })
    elif dialect == "sqlite":
        capabilities.update({
            'supports_json': True,  # SQLite 3.38+ (JSON1 extension)
            'supports_array': False,
            'supports_uuid': False,  # Can use BLOB
            'supports_boolean': False,  # Uses INTEGER
            'supports_enum': False,
            'supports_set': False,
            'supports_spatial': False,  # With extensions
            'supports_full_text': True,  # FTS5 extension
            'supports_xml': False,
            'supports_character_semantics': False,
            'supports_identity': True,  # AUTOINCREMENT
            'supports_sequence': False,
        })
    else:
        # Conservative defaults
        capabilities.update({
            'supports_json': False,
            'supports_array': False,
            'supports_uuid': False,
            'supports_boolean': False,
            'supports_enum': False,
            'supports_set': False,
            'supports_spatial': False,
            'supports_full_text': False,
            'supports_xml': False,
            'supports_character_semantics': False,
            'supports_identity': False,
            'supports_sequence': False,
        })
    
    return capabilities


def get_index_capabilities(dialect: str) -> Dict[str, bool]:
    """
    Get index creation and management capabilities for the dialect.
    
    Args:
        dialect: Database dialect name
        
    Returns:
        Index capability flags
    """
    capabilities = {}
    
    if dialect == "oracle":
        capabilities.update({
            'supports_functional_index': True,
            'supports_partial_index': True,  # Oracle 12c+
            'supports_unique_index': True,
            'supports_composite_index': True,
            'supports_bitmap_index': True,
            'supports_full_text_index': True,  # Oracle Text
            'supports_spatial_index': True,  # Oracle Spatial
            'supports_concurrent_index_creation': True,
            'supports_index_include_columns': True,  # Oracle 12c+
        })
    elif dialect == "postgresql":
        capabilities.update({
            'supports_functional_index': True,
            'supports_partial_index': True,
            'supports_unique_index': True,
            'supports_composite_index': True,
            'supports_bitmap_index': False,
            'supports_full_text_index': True,
            'supports_spatial_index': True,  # PostGIS
            'supports_concurrent_index_creation': True,
            'supports_index_include_columns': True,  # PostgreSQL 11+
        })
    elif dialect == "mssql":
        capabilities.update({
            'supports_functional_index': False,  # Computed columns
            'supports_partial_index': True,  # Filtered indexes
            'supports_unique_index': True,
            'supports_composite_index': True,
            'supports_bitmap_index': False,
            'supports_full_text_index': True,
            'supports_spatial_index': True,
            'supports_concurrent_index_creation': True,  # ONLINE option
            'supports_index_include_columns': True,
        })
    elif dialect == "mysql":
        capabilities.update({
            'supports_functional_index': True,  # MySQL 8.0+
            'supports_partial_index': False,
            'supports_unique_index': True,
            'supports_composite_index': True,
            'supports_bitmap_index': False,
            'supports_full_text_index': True,
            'supports_spatial_index': True,
            'supports_concurrent_index_creation': True,  # ALGORITHM=INPLACE
            'supports_index_include_columns': False,
        })
    elif dialect == "sqlite":
        capabilities.update({
            'supports_functional_index': True,
            'supports_partial_index': True,
            'supports_unique_index': True,
            'supports_composite_index': True,
            'supports_bitmap_index': False,
            'supports_full_text_index': True,  # FTS5
            'supports_spatial_index': False,  # With extensions
            'supports_concurrent_index_creation': True,  # Single-user
            'supports_index_include_columns': False,
        })
    else:
        # Conservative defaults
        capabilities.update({
            'supports_functional_index': False,
            'supports_partial_index': False,
            'supports_unique_index': True,
            'supports_composite_index': True,
            'supports_bitmap_index': False,
            'supports_full_text_index': False,
            'supports_spatial_index': False,
            'supports_concurrent_index_creation': False,
            'supports_index_include_columns': False,
        })
    
    return capabilities


def get_constraint_capabilities(dialect: str) -> Dict[str, bool]:
    """
    Get constraint management capabilities for the dialect.
    
    Args:
        dialect: Database dialect name
        
    Returns:
        Constraint capability flags
    """
    capabilities = {}
    
    if dialect == "oracle":
        capabilities.update({
            'supports_primary_key': True,
            'supports_foreign_key': True,
            'supports_unique_constraint': True,
            'supports_check_constraint': True,
            'supports_not_null_constraint': True,
            'supports_deferrable_constraint': True,
            'supports_constraint_validation': True,
            'supports_named_constraints': True,
            'supports_constraint_disable': True,
        })
    elif dialect == "postgresql":
        capabilities.update({
            'supports_primary_key': True,
            'supports_foreign_key': True,
            'supports_unique_constraint': True,
            'supports_check_constraint': True,
            'supports_not_null_constraint': True,
            'supports_deferrable_constraint': True,
            'supports_constraint_validation': True,
            'supports_named_constraints': True,
            'supports_constraint_disable': True,
        })
    elif dialect == "mssql":
        capabilities.update({
            'supports_primary_key': True,
            'supports_foreign_key': True,
            'supports_unique_constraint': True,
            'supports_check_constraint': True,
            'supports_not_null_constraint': True,
            'supports_deferrable_constraint': False,
            'supports_constraint_validation': True,
            'supports_named_constraints': True,
            'supports_constraint_disable': True,
        })
    elif dialect == "mysql":
        capabilities.update({
            'supports_primary_key': True,
            'supports_foreign_key': True,
            'supports_unique_constraint': True,
            'supports_check_constraint': True,  # MySQL 8.0+
            'supports_not_null_constraint': True,
            'supports_deferrable_constraint': False,
            'supports_constraint_validation': True,
            'supports_named_constraints': True,
            'supports_constraint_disable': True,
        })
    elif dialect == "sqlite":
        capabilities.update({
            'supports_primary_key': True,
            'supports_foreign_key': True,
            'supports_unique_constraint': True,
            'supports_check_constraint': True,
            'supports_not_null_constraint': True,
            'supports_deferrable_constraint': False,
            'supports_constraint_validation': True,
            'supports_named_constraints': False,  # Limited
            'supports_constraint_disable': False,
        })
    else:
        # Conservative defaults
        capabilities.update({
            'supports_primary_key': True,
            'supports_foreign_key': False,
            'supports_unique_constraint': True,
            'supports_check_constraint': False,
            'supports_not_null_constraint': True,
            'supports_deferrable_constraint': False,
            'supports_constraint_validation': False,
            'supports_named_constraints': False,
            'supports_constraint_disable': False,
        })
    
    return capabilities


def is_safe_ddl_operation(
    operation_type: str,
    engine_info: EngineInfo
) -> bool:
    """
    Check if a DDL operation is safe for the given engine.
    
    Args:
        operation_type: Type of DDL operation
        engine_info: Engine information and capabilities
        
    Returns:
        True if operation is considered safe
    """
    capabilities = engine_info.capabilities
    
    # Map operation types to capability flags
    operation_capability_map = {
        'ADD_COLUMN': 'supports_add_column',
        'DROP_COLUMN': 'supports_drop_column',
        'ALTER_COLUMN': 'supports_alter_column_type',
        'RENAME_COLUMN': 'supports_rename_column',
        'RENAME_TABLE': 'supports_rename_table',
        'ADD_CONSTRAINT': 'supports_add_constraint',
        'DROP_CONSTRAINT': 'supports_drop_constraint',
        'ADD_INDEX': 'supports_add_column',  # Index creation similar to column addition
    }
    
    capability = operation_capability_map.get(operation_type)
    if capability:
        return capabilities.get(capability, False)
    
    # Default to unsafe for unknown operations
    return False


def get_ddl_safety_recommendations(engine_info: EngineInfo) -> List[str]:
    """
    Get safety recommendations for DDL operations on the engine.
    
    Args:
        engine_info: Engine information and capabilities
        
    Returns:
        List of safety recommendations
    """
    recommendations = []
    capabilities = engine_info.capabilities
    dialect = engine_info.dialect
    
    # General recommendations
    if not capabilities.get('supports_transactions', False):
        recommendations.append("Engine does not support transactions - DDL operations cannot be rolled back")
    
    if not capabilities.get('supports_savepoints', False):
        recommendations.append("Engine does not support savepoints - limited error recovery")
    
    if not capabilities.get('supports_online_ddl', False):
        recommendations.append("Engine does not support online DDL - operations may block access")
    
    # Dialect-specific recommendations
    if dialect == "oracle":
        recommendations.append("Oracle DDL operations auto-commit - ensure data is committed before schema changes")
        recommendations.append("Consider using DBMS_REDEFINITION for complex table changes")
    elif dialect == "postgresql":
        recommendations.append("PostgreSQL supports transactional DDL - operations can be rolled back")
        recommendations.append("Consider using LOCK TABLE for concurrent access control")
    elif dialect == "mssql":
        recommendations.append("SQL Server supports online DDL in Enterprise Edition")
        recommendations.append("Consider using WITH (ONLINE = ON) for minimal locking")
    elif dialect == "mysql":
        recommendations.append("MySQL DDL operations auto-commit - ensure data is committed before schema changes")
        recommendations.append("Consider using ALGORITHM=INPLACE for online DDL (MySQL 5.6+)")
    elif dialect == "sqlite":
        recommendations.append("SQLite has limited DDL support - complex changes may require table recreation")
        recommendations.append("Consider using VACUUM after schema changes")
    
    return recommendations
