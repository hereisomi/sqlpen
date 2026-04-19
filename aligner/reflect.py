"""
Table reflection for DataFrame-to-SQL alignment.

Reflects database table structure into canonical TableSpec objects,
with Oracle-specific enrichment and multi-dialect support.
"""

import sqlalchemy as sa
from sqlalchemy import inspect
from typing import Dict, Any, Optional, List, Tuple
from .models import TableSpec, ColumnSpec, ConstraintSpec, IndexSpec
from .type_system import infer_type_family, extract_limits
from .engine import get_engine_info


def reflect_table_spec(
    engine: sa.engine.Engine,
    schema: str,
    table_name: str
) -> TableSpec:
    """
    Reflect table structure from database into canonical TableSpec.
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name
        table_name: Table name
        
    Returns:
        TableSpec with complete table definition
    """
    inspector = inspect(engine)
    engine_info = get_engine_info(engine)
    
    # Get basic table information
    columns = reflect_columns(inspector, schema, table_name, engine_info.dialect)
    constraints = reflect_constraints(engine, inspector, schema, table_name, engine_info.dialect)
    indexes = reflect_indexes(engine, inspector, schema, table_name, engine_info.dialect)
    
    # Get table comment (if supported)
    table_comment = get_table_comment(engine, schema, table_name, engine_info.dialect)
    
    # Oracle-specific enrichment
    if engine_info.dialect.lower() == "oracle":
        columns = enrich_oracle_columns(engine, schema, table_name, columns)
        constraints = enrich_oracle_constraints(engine, schema, table_name, constraints)
    
    return TableSpec(
        schema=schema,
        name=table_name,
        columns=columns,
        constraints=constraints,
        indexes=indexes,
        comment=table_comment
    )


def reflect_columns(
    inspector: sa.engine.Inspector,
    schema: str,
    table_name: str,
    dialect: str
) -> Dict[str, ColumnSpec]:
    """
    Reflect column definitions from database.
    
    Args:
        inspector: SQLAlchemy inspector
        schema: Schema name
        table_name: Table name
        dialect: Database dialect
        
    Returns:
        Dictionary of column specifications
    """
    columns = {}
    
    try:
        raw_columns = inspector.get_columns(table_name, schema=schema)
    except Exception:
        # Fallback for databases that don't support schema parameter
        try:
            raw_columns = inspector.get_columns(table_name)
        except Exception as e:
            raise ValueError(f"Failed to reflect columns for {schema}.{table_name}: {e}")
    
    for col_info in raw_columns:
        col_name = col_info['name']
        col_type = col_info['type']
        
        # Infer type family and extract limits
        type_family = infer_type_family(col_type, dialect)
        limits = extract_limits(col_type, dialect)
        
        # Handle nullable
        nullable = col_info.get('nullable', True)
        
        # Handle default value
        default_value = col_info.get('default', None)
        
        # Handle auto increment
        auto_increment = col_info.get('autoincrement', False)
        
        # Handle comment (if available)
        comment = col_info.get('comment', None)
        
        column_spec = ColumnSpec(
            name=col_name,
            type_family=type_family.value,
            sql_type=str(col_type),
            nullable=nullable,
            default_value=default_value,
            max_length=limits.get('max_length'),
            precision=limits.get('precision'),
            scale=limits.get('scale'),
            timezone=limits.get('timezone'),
            auto_increment=auto_increment,
            comment=comment,
            char_semantics=limits.get('char_semantics')
        )
        
        columns[col_name] = column_spec
    
    return columns


def reflect_constraints(
    engine: sa.engine.Engine,
    inspector: sa.engine.Inspector,
    schema: str,
    table_name: str,
    dialect: str
) -> Dict[str, ConstraintSpec]:
    """
    Reflect constraint definitions from database.
    
    Args:
        engine: SQLAlchemy engine
        inspector: SQLAlchemy inspector
        schema: Schema name
        table_name: Table name
        dialect: Database dialect
        
    Returns:
        Dictionary of constraint specifications
    """
    constraints = {}
    
    try:
        # Primary key constraints
        pk_info = inspector.get_pk_constraint(table_name, schema=schema)
        if pk_info and pk_info.get('constrained_columns'):
            constraint = ConstraintSpec(
                name=pk_info.get('name', f'pk_{table_name}'),
                type="PRIMARY_KEY",
                columns=pk_info['constrained_columns']
            )
            constraints[constraint.name] = constraint
    except Exception:
        pass
    
    try:
        # Foreign key constraints
        fk_info = inspector.get_foreign_keys(table_name, schema=schema)
        for fk in fk_info:
            constraint = ConstraintSpec(
                name=fk['name'],
                type="FOREIGN_KEY",
                columns=fk['constrained_columns'],
                references_table=fk['referred_table'],
                references_columns=fk['referred_columns']
            )
            constraints[constraint.name] = constraint
    except Exception:
        pass
    
    try:
        # Unique constraints
        uc_info = inspector.get_unique_constraints(table_name, schema=schema)
        for uc in uc_info:
            constraint = ConstraintSpec(
                name=uc['name'],
                type="UNIQUE",
                columns=uc['constrained_columns']
            )
            constraints[constraint.name] = constraint
    except Exception:
        pass
    
    # Check constraints (dialect-specific reflection)
    if dialect.lower() in ['postgresql', 'oracle']:
        check_constraints = reflect_check_constraints(engine, schema, table_name, dialect)
        constraints.update(check_constraints)
    
    return constraints


def reflect_check_constraints(
    engine: sa.engine.Engine,
    schema: str,
    table_name: str,
    dialect: str
) -> Dict[str, ConstraintSpec]:
    """
    Reflect check constraints (dialect-specific implementation).
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name
        table_name: Table name
        dialect: Database dialect
        
    Returns:
        Dictionary of check constraint specifications
    """
    constraints = {}
    
    if dialect.lower() == "postgresql":
        try:
            with engine.connect() as conn:
                table_oid_query = sa.text("""
                    SELECT oid FROM pg_class 
                    WHERE relname = :table_name 
                    AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = :schema)
                """)
                table_oid = conn.execute(table_oid_query, {
                    'table_name': table_name, 'schema': schema
                }).scalar()
                
                if table_oid:
                    result = conn.execute(sa.text("""
                        SELECT conname, consrc FROM pg_constraint 
                        WHERE conrelid = :table_oid AND contype = 'c'
                    """), {'table_oid': table_oid})
                    for row in result:
                        c = ConstraintSpec(name=row[0], type="CHECK", columns=[], definition=row[1])
                        constraints[c.name] = c
        except Exception:
            pass
    
    elif dialect.lower() == "oracle":
        try:
            with engine.connect() as conn:
                result = conn.execute(sa.text("""
                    SELECT constraint_name, search_condition
                    FROM all_constraints
                    WHERE owner = :schema AND table_name = :table_name AND constraint_type = 'C'
                """), {'schema': schema.upper(), 'table_name': table_name.upper()})
                for row in result:
                    c = ConstraintSpec(name=row[0], type="CHECK", columns=[], definition=row[1])
                    constraints[c.name] = c
        except Exception:
            pass
    
    return constraints


def reflect_indexes(
    engine: sa.engine.Engine,
    inspector: sa.engine.Inspector,
    schema: str,
    table_name: str,
    dialect: str
) -> Dict[str, IndexSpec]:
    """
    Reflect index definitions from database.
    
    Args:
        engine: SQLAlchemy engine
        inspector: SQLAlchemy inspector
        schema: Schema name
        table_name: Table name
        dialect: Database dialect
        
    Returns:
        Dictionary of index specifications
    """
    indexes = {}
    
    try:
        index_info = inspector.get_indexes(table_name, schema=schema)
        for idx in index_info:
            # Skip indexes that are automatically created for constraints
            if not idx.get('unique', False) and any(
                idx['name'].startswith(prefix) 
                for prefix in ['pk_', 'uk_', 'fk_']
            ):
                continue
            
            index_spec = IndexSpec(
                name=idx['name'],
                columns=idx['column_names'],
                unique=idx.get('unique', False),
                type=idx.get('type', 'BTREE'),
                definition=idx.get('ddl')
            )
            indexes[index_spec.name] = index_spec
    except Exception:
        pass
    
    # Oracle-specific index reflection
    if dialect.lower() == "oracle":
        oracle_indexes = reflect_oracle_indexes(engine, schema, table_name)
        indexes.update(oracle_indexes)
    
    return indexes


def reflect_oracle_indexes(
    engine: sa.engine.Engine,
    schema: str,
    table_name: str
) -> Dict[str, IndexSpec]:
    """
    Reflect Oracle-specific index information.
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name
        table_name: Table name
        
    Returns:
        Dictionary of Oracle index specifications
    """
    indexes = {}
    
    try:
        with engine.connect() as conn:
            # Get detailed index information
            query = sa.text("""
                SELECT i.index_name, i.uniqueness, i.index_type,
                       ic.column_name, ic.column_position
                FROM all_indexes i
                JOIN all_ind_columns ic ON i.index_name = ic.index_name
                WHERE i.table_owner = :schema
                AND i.table_name = :table_name
                ORDER BY i.index_name, ic.column_position
            """)
            
            result = conn.execute(query, {
                'schema': schema.upper(),
                'table_name': table_name.upper()
            })
            
            current_index = None
            columns = []
            
            for row in result:
                index_name, uniqueness, index_type, column_name, column_position = row
                
                if current_index != index_name:
                    # Save previous index
                    if current_index and columns:
                        index_spec = IndexSpec(
                            name=current_index,
                            columns=columns,
                            unique=(uniqueness == 'UNIQUE'),
                            type=index_type
                        )
                        indexes[index_spec.name] = index_spec
                    
                    # Start new index
                    current_index = index_name
                    columns = [column_name]
                else:
                    columns.append(column_name)
            
            # Save last index
            if current_index and columns:
                index_spec = IndexSpec(
                    name=current_index,
                    columns=columns,
                    unique=(uniqueness == 'UNIQUE'),
                    type=index_type
                )
                indexes[index_spec.name] = index_spec
                
    except Exception:
        pass
    
    return indexes


def get_table_comment(
    engine: sa.engine.Engine,
    schema: str,
    table_name: str,
    dialect: str
) -> Optional[str]:
    """
    Get table comment if supported by the database.
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name
        table_name: Table name
        dialect: Database dialect
        
    Returns:
        Table comment or None
    """
    try:
        with engine.connect() as conn:
            if dialect.lower() == "postgresql":
                result = conn.execute(sa.text("""
                    SELECT obj_description(c.oid)
                    FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE n.nspname = :schema AND c.relname = :table_name
                """), {'schema': schema, 'table_name': table_name})
                return result.scalar()
            elif dialect.lower() == "oracle":
                result = conn.execute(sa.text("""
                    SELECT comments FROM all_tab_comments
                    WHERE owner = :schema AND table_name = :table_name
                """), {'schema': schema.upper(), 'table_name': table_name.upper()})
                return result.scalar()
            elif dialect.lower() == "mysql":
                result = conn.execute(sa.text("""
                    SELECT table_comment FROM information_schema.tables
                    WHERE table_schema = :schema AND table_name = :table_name
                """), {'schema': schema, 'table_name': table_name})
                return result.scalar()
    except Exception:
        pass
    return None


def enrich_oracle_columns(
    engine: sa.engine.Engine,
    schema: str,
    table_name: str,
    columns: Dict[str, ColumnSpec]
) -> Dict[str, ColumnSpec]:
    """
    Enrich Oracle column specifications with additional metadata.
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name
        table_name: Table name
        columns: Existing column specifications
        
    Returns:
        Enriched column specifications
    """
    try:
        with engine.connect() as conn:
            # Get column comments
            comment_query = sa.text("""
                SELECT column_name, comments
                FROM all_col_comments
                WHERE owner = :schema
                AND table_name = :table_name
            """)
            comment_result = conn.execute(comment_query, {
                'schema': schema.upper(),
                'table_name': table_name.upper()
            })
            
            for row in comment_result:
                col_name, comment = row
                if col_name in columns:
                    columns[col_name].comment = comment
            
            # Get identity column information
            identity_query = sa.text("""
                SELECT column_name, generation_type, identity_options
                FROM all_tab_identity_cols
                WHERE owner = :schema
                AND table_name = :table_name
            """)
            identity_result = conn.execute(identity_query, {
                'schema': schema.upper(),
                'table_name': table_name.upper()
            })
            
            for row in identity_result:
                col_name, gen_type, identity_options = row
                if col_name in columns:
                    columns[col_name].identity = {
                        'generation_type': gen_type,
                        'options': identity_options
                    }
                    columns[col_name].auto_increment = True
            
    except Exception:
        # Enrichment is optional, continue if it fails
        pass
    
    return columns


def enrich_oracle_constraints(
    engine: sa.engine.Engine,
    schema: str,
    table_name: str,
    constraints: Dict[str, ConstraintSpec]
) -> Dict[str, ConstraintSpec]:
    """
    Enrich Oracle constraint specifications with additional metadata.
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name
        table_name: Table name
        constraints: Existing constraint specifications
        
    Returns:
        Enriched constraint specifications
    """
    try:
        with engine.connect() as conn:
            # Get constraint status and validation information
            status_query = sa.text("""
                SELECT constraint_name, status, validated, deferrable, deferred
                FROM all_constraints
                WHERE owner = :schema
                AND table_name = :table_name
            """)
            status_result = conn.execute(status_query, {
                'schema': schema.upper(),
                'table_name': table_name.upper()
            })
            
            for row in status_result:
                constraint_name, status, validated, deferrable, deferred = row
                if constraint_name in constraints:
                    constraints[constraint_name].definition = (
                        f"STATUS: {status}, VALIDATED: {validated}, "
                        f"DEFERRABLE: {deferrable}, DEFERRED: {deferred}"
                    )
                    
    except Exception:
        # Enrichment is optional, continue if it fails
        pass
    
    return constraints


def reflect_all_tables(
    engine: sa.engine.Engine,
    schema: Optional[str] = None
) -> Dict[str, TableSpec]:
    """
    Reflect all tables in a schema.
    
    Args:
        engine: SQLAlchemy engine
        schema: Schema name (None for default schema)
        
    Returns:
        Dictionary of table specifications keyed by table name
    """
    inspector = inspect(engine)
    
    try:
        if schema:
            table_names = inspector.get_table_names(schema=schema)
        else:
            table_names = inspector.get_table_names()
    except Exception:
        # Fallback for databases that don't support schema parameter
        table_names = inspector.get_table_names()
    
    tables = {}
    
    for table_name in table_names:
        try:
            if schema:
                table_spec = reflect_table_spec(engine, schema, table_name)
            else:
                # Determine default schema for the dialect
                default_schema = get_default_schema(engine)
                table_spec = reflect_table_spec(engine, default_schema, table_name)
            
            tables[table_name] = table_spec
        except Exception as e:
            # Skip tables that can't be reflected
            continue
    
    return tables


def get_default_schema(engine: sa.engine.Engine) -> str:
    """
    Get the default schema for the database dialect.
    
    Args:
        engine: SQLAlchemy engine
        
    Returns:
        Default schema name
    """
    dialect = engine.dialect.name.lower()
    
    if dialect == "oracle":
        try:
            with engine.connect() as conn:
                result = conn.execute(sa.text("SELECT USER FROM DUAL"))
                return result.scalar().upper()
        except Exception:
            return "SYS"
    elif dialect == "postgresql":
        try:
            with engine.connect() as conn:
                result = conn.execute(sa.text("SELECT current_schema()"))
                return result.scalar()
        except Exception:
            return "public"
    elif dialect == "mssql":
        try:
            with engine.connect() as conn:
                result = conn.execute(sa.text("SELECT SCHEMA_NAME()"))
                return result.scalar()
        except Exception:
            return "dbo"
    elif dialect == "mysql":
        try:
            with engine.connect() as conn:
                result = conn.execute(sa.text("SELECT DATABASE()"))
                return result.scalar()
        except Exception:
            return "mysql"
    elif dialect == "sqlite":
        return "main"
    else:
        return "public"
