"""
DDL execution for DataFrame-to-SQL alignment.

Executes DDL plans safely with proper error handling, transaction management,
and dialect-specific considerations.
"""

import time
import logging
from typing import List, Dict, Any, Optional
import sqlalchemy as sa
from sqlalchemy import text
from .models import DdlPlan, DdlAction, ExecutionReport, Issue, IssueCode, Severity
from .policies import AlignmentPolicies, DEFAULT_POLICIES
from .engine import get_engine_info, is_safe_ddl_operation

# Set up logging
logger = logging.getLogger(__name__)


def apply_ddl_plan(
    engine: sa.engine.Engine,
    ddl_plan: DdlPlan,
    dry_run: bool = True,
    lock_timeout_seconds: int = 30,
    statement_timeout_seconds: int = 300
) -> ExecutionReport:
    """
    Apply DDL plan to the database with safety controls.
    
    Args:
        engine: SQLAlchemy engine
        ddl_plan: DDL plan to execute
        dry_run: If True, only simulate execution without making changes
        lock_timeout_seconds: Timeout for acquiring locks
        statement_timeout_seconds: Timeout for individual statements
        
    Returns:
        ExecutionReport with results
    """
    start_time = time.time()
    engine_info = get_engine_info(engine)
    
    executed_actions = []
    failed_actions = []
    issues = []
    
    # Validate plan against engine capabilities
    for action in ddl_plan.actions:
        if not is_safe_ddl_operation(action.action_type, engine_info):
            issues.append(Issue(
                code=IssueCode.UNSAFE_DDL_OPERATION,
                severity=Severity.ERROR,
                message=f"Unsafe DDL operation '{action.action_type}' for {engine_info.dialect}",
                context={'action': action.action_type, 'dialect': engine_info.dialect}
            ))
            failed_actions.append(action)
    
    # If there are unsafe operations and we're not in dry run, fail
    if not dry_run and any(issue.severity == Severity.ERROR for issue in issues):
        return ExecutionReport(
            ddl_plan=ddl_plan,
            executed_actions=executed_actions,
            failed_actions=failed_actions,
            execution_time_seconds=time.time() - start_time,
            issues=issues
        )
    
    # Execute actions
    with engine.connect() as conn:
        # Set up timeouts if supported
        setup_timeouts(conn, engine_info.dialect, lock_timeout_seconds, statement_timeout_seconds)
        
        # Begin transaction if supported for DDL
        transaction = None
        if engine_info.capabilities.get('requires_transaction_for_ddl', False):
            transaction = conn.begin()
        
        try:
            for action in ddl_plan.actions:
                try:
                    if dry_run:
                        # Simulate execution
                        logger.info(f"[DRY RUN] Would execute: {action.action_type} on {action.table_name}")
                        executed_actions.append(action)
                    else:
                        # Actually execute
                        execute_ddl_action(conn, action, engine_info)
                        executed_actions.append(action)
                        logger.info(f"Executed: {action.action_type} on {action.table_name}")
                
                except Exception as e:
                    error_msg = f"Failed to execute {action.action_type} on {action.table_name}: {str(e)}"
                    logger.error(error_msg)
                    
                    issues.append(Issue(
                        code=IssueCode.DDL_EXECUTION_FAILED,
                        severity=Severity.ERROR,
                        message=error_msg,
                        context={
                            'action': action.action_type,
                            'table': action.table_name,
                            'error': str(e)
                        }
                    ))
                    failed_actions.append(action)
                    
                    # For non-dry run, rollback on failure if transaction is active
                    if not dry_run and transaction:
                        transaction.rollback()
                        break
            
            # Commit transaction if successful
            if not dry_run and transaction:
                transaction.commit()
                
        except Exception as e:
            # Rollback on transaction error
            if not dry_run and transaction:
                try:
                    transaction.rollback()
                except Exception:
                    pass
            
            issues.append(Issue(
                code=IssueCode.DDL_EXECUTION_FAILED,
                severity=Severity.ERROR,
                message=f"Transaction failed: {str(e)}",
                context={'error': str(e)}
            ))
    
    execution_time = time.time() - start_time
    
    return ExecutionReport(
        ddl_plan=ddl_plan,
        executed_actions=executed_actions,
        failed_actions=failed_actions,
        execution_time_seconds=execution_time,
        issues=issues
    )


def setup_timeouts(
    conn: sa.engine.Connection,
    dialect: str,
    lock_timeout_seconds: int,
    statement_timeout_seconds: int
) -> None:
    """
    Set up session timeouts for DDL operations.
    
    Args:
        conn: Database connection
        dialect: Database dialect name
        lock_timeout_seconds: Lock timeout in seconds
        statement_timeout_seconds: Statement timeout in seconds
    """
    try:
        if dialect.lower() == "oracle":
            # Oracle uses different timeout mechanisms
            if lock_timeout_seconds > 0:
                conn.execute(text(f"ALTER SESSION SET ddl_lock_timeout = {lock_timeout_seconds}"))
            if statement_timeout_seconds > 0:
                # Oracle doesn't have a simple statement timeout
                pass
        
        elif dialect.lower() == "postgresql":
            if lock_timeout_seconds > 0:
                conn.execute(text(f"SET lock_timeout = '{lock_timeout_seconds}s'"))
            if statement_timeout_seconds > 0:
                conn.execute(text(f"SET statement_timeout = '{statement_timeout_seconds}s'"))
        
        elif dialect.lower() == "mssql":
            if lock_timeout_seconds > 0:
                conn.execute(text(f"SET LOCK_TIMEOUT {lock_timeout_seconds * 1000}"))  # milliseconds
            # SQL Server doesn't have a simple statement timeout
        
        elif dialect.lower() == "mysql":
            if lock_timeout_seconds > 0:
                conn.execute(text(f"SET SESSION innodb_lock_wait_timeout = {lock_timeout_seconds}"))
            if statement_timeout_seconds > 0:
                conn.execute(text(f"SET SESSION max_execution_time = {statement_timeout_seconds}"))
        
        elif dialect.lower() == "sqlite":
            # SQLite doesn't support these timeouts
            pass
    
    except Exception as e:
        logger.warning(f"Failed to set timeouts: {str(e)}")


def execute_ddl_action(
    conn: sa.engine.Connection,
    action: DdlAction,
    engine_info
) -> None:
    """
    Execute a single DDL action.
    
    Args:
        conn: Database connection
        action: DDL action to execute
        engine_info: Engine information and capabilities
    """
    dialect = engine_info.dialect.lower()
    
    if action.action_type == "ADD_COLUMN":
        execute_add_column(conn, action, dialect)
    
    elif action.action_type == "DROP_COLUMN":
        execute_drop_column(conn, action, dialect)
    
    elif action.action_type == "ALTER_COLUMN":
        execute_alter_column(conn, action, dialect)
    
    elif action.action_type == "ADD_INDEX":
        execute_add_index(conn, action, dialect)
    
    elif action.action_type == "DROP_INDEX":
        execute_drop_index(conn, action, dialect)
    
    else:
        raise ValueError(f"Unknown DDL action type: {action.action_type}")


def execute_add_column(
    conn: sa.engine.Connection,
    action: DdlAction,
    dialect: str
) -> None:
    """Execute ADD COLUMN DDL."""
    if dialect == "oracle":
        sql = f"ALTER TABLE {action.table_name} ADD {action.column_name} {action.sql_type}"
    elif dialect == "postgresql":
        sql = f"ALTER TABLE {action.table_name} ADD COLUMN {action.column_name} {action.sql_type}"
    elif dialect == "mssql":
        sql = f"ALTER TABLE {action.table_name} ADD {action.column_name} {action.sql_type}"
    elif dialect == "mysql":
        sql = f"ALTER TABLE {action.table_name} ADD COLUMN {action.column_name} {action.sql_type}"
    elif dialect == "sqlite":
        # SQLite doesn't support ADD COLUMN with constraints in a simple way
        sql = f"ALTER TABLE {action.table_name} ADD COLUMN {action.column_name} {action.sql_type}"
    else:
        sql = f"ALTER TABLE {action.table_name} ADD COLUMN {action.column_name} {action.sql_type}"
    
    conn.execute(text(sql))


def execute_drop_column(
    conn: sa.engine.Connection,
    action: DdlAction,
    dialect: str
) -> None:
    """Execute DROP COLUMN DDL."""
    if dialect == "sqlite":
        raise NotImplementedError("SQLite doesn't support DROP COLUMN directly")
    
    if dialect == "oracle":
        sql = f"ALTER TABLE {action.table_name} DROP COLUMN {action.column_name}"
    elif dialect == "postgresql":
        sql = f"ALTER TABLE {action.table_name} DROP COLUMN {action.column_name}"
    elif dialect == "mssql":
        sql = f"ALTER TABLE {action.table_name} DROP COLUMN {action.column_name}"
    elif dialect == "mysql":
        sql = f"ALTER TABLE {action.table_name} DROP COLUMN {action.column_name}"
    else:
        sql = f"ALTER TABLE {action.table_name} DROP COLUMN {action.column_name}"
    
    conn.execute(text(sql))


def execute_alter_column(
    conn: sa.engine.Connection,
    action: DdlAction,
    dialect: str
) -> None:
    """Execute ALTER COLUMN DDL."""
    if dialect == "oracle":
        sql = f"ALTER TABLE {action.table_name} MODIFY {action.column_name} {action.sql_type}"
    elif dialect == "postgresql":
        sql = f"ALTER TABLE {action.table_name} ALTER COLUMN {action.column_name} TYPE {action.sql_type}"
    elif dialect == "mssql":
        sql = f"ALTER TABLE {action.table_name} ALTER COLUMN {action.column_name} {action.sql_type}"
    elif dialect == "mysql":
        sql = f"ALTER TABLE {action.table_name} MODIFY COLUMN {action.column_name} {action.sql_type}"
    elif dialect == "sqlite":
        raise NotImplementedError("SQLite doesn't support ALTER COLUMN directly")
    else:
        sql = f"ALTER TABLE {action.table_name} ALTER COLUMN {action.column_name} {action.sql_type}"
    
    conn.execute(text(sql))


def execute_add_index(
    conn: sa.engine.Connection,
    action: DdlAction,
    dialect: str
) -> None:
    """Execute ADD INDEX DDL."""
    if action.definition:
        # Use provided definition
        sql = action.definition
    else:
        # Generate basic index creation
        index_name = f"idx_{action.table_name.replace('.', '_')}_{action.column_name}"
        sql = f"CREATE INDEX {index_name} ON {action.table_name} ({action.column_name})"
    
    conn.execute(text(sql))


def execute_drop_index(
    conn: sa.engine.Connection,
    action: DdlAction,
    dialect: str
) -> None:
    """Execute DROP INDEX DDL."""
    if dialect == "oracle":
        sql = f"DROP INDEX {action.column_name}"  # Oracle uses index name directly
    elif dialect == "postgresql":
        sql = f"DROP INDEX {action.column_name}"
    elif dialect == "mssql":
        sql = f"DROP INDEX {action.column_name} ON {action.table_name}"
    elif dialect == "mysql":
        sql = f"DROP INDEX {action.column_name} ON {action.table_name}"
    elif dialect == "sqlite":
        sql = f"DROP INDEX IF EXISTS {action.column_name}"
    else:
        sql = f"DROP INDEX {action.column_name}"
    
    conn.execute(text(sql))


def validate_ddl_before_execution(
    engine: sa.engine.Engine,
    ddl_plan: DdlPlan,
    policies: AlignmentPolicies
) -> List[Issue]:
    """
    Validate DDL plan before execution.
    
    Args:
        engine: SQLAlchemy engine
        ddl_plan: DDL plan to validate
        policies: Alignment policies
        
    Returns:
        List of validation issues
    """
    issues = []
    engine_info = get_engine_info(engine)
    
    # Check each action against engine capabilities
    for action in ddl_plan.actions:
        if not is_safe_ddl_operation(action.action_type, engine_info):
            severity = Severity.ERROR if policies.strict_mode else Severity.WARNING
            issues.append(Issue(
                code=IssueCode.UNSAFE_DDL_OPERATION,
                severity=severity,
                message=f"DDL operation '{action.action_type}' may not be safe for {engine_info.dialect}",
                context={
                    'action': action.action_type,
                    'table': action.table_name,
                    'dialect': engine_info.dialect
                }
            ))
    
    # Check for potentially destructive operations
    destructive_actions = [action for action in ddl_plan.actions if action.action_type in ["DROP_COLUMN", "DROP_INDEX"]]
    if destructive_actions and not policies.ddl.dry_run:
        issues.append(Issue(
            code=IssueCode.UNSAFE_DDL_OPERATION,
            severity=Severity.WARNING,
            message=f"Plan contains {len(destructive_actions)} potentially destructive operations",
            context={'destructive_actions': [a.action_type for a in destructive_actions]}
        ))
    
    # Check batch size limits
    if policies.ddl.batch_ddl and len(ddl_plan.actions) > policies.ddl.max_batch_size:
        issues.append(Issue(
            code=IssueCode.UNSAFE_DDL_OPERATION,
            severity=Severity.WARNING,
            message=f"DDL plan exceeds batch size limit: {len(ddl_plan.actions)} > {policies.ddl.max_batch_size}"
        ))
    
    return issues


def get_execution_summary(report: ExecutionReport) -> Dict[str, Any]:
    """
    Get a comprehensive summary of DDL execution results.
    
    Args:
        report: Execution report
        
    Returns:
        Summary dictionary
    """
    summary = {
        'total_actions': len(report.ddl_plan.actions),
        'executed_actions': len(report.executed_actions),
        'failed_actions': len(report.failed_actions),
        'success_rate': report.success_rate,
        'execution_time_seconds': report.execution_time_seconds,
        'has_errors': any(issue.severity == Severity.ERROR for issue in report.issues),
        'has_warnings': any(issue.severity == Severity.WARNING for issue in report.issues),
    }
    
    # Action type breakdown
    action_counts = {}
    for action in report.executed_actions:
        action_counts[action.action_type] = action_counts.get(action.action_type, 0) + 1
    
    failed_counts = {}
    for action in report.failed_actions:
        failed_counts[action.action_type] = failed_counts.get(action.action_type, 0) + 1
    
    summary['executed_by_type'] = action_counts
    summary['failed_by_type'] = failed_counts
    
    # Error summary
    error_issues = [issue for issue in report.issues if issue.severity == Severity.ERROR]
    warning_issues = [issue for issue in report.issues if issue.severity == Severity.WARNING]
    
    summary['error_count'] = len(error_issues)
    summary['warning_count'] = len(warning_issues)
    summary['errors'] = [issue.message for issue in error_issues]
    summary['warnings'] = [issue.message for issue in warning_issues]
    
    # Status interpretation
    if report.success_rate == 1.0:
        summary['status'] = "SUCCESS"
        summary['message'] = "All DDL actions executed successfully"
    elif report.success_rate >= 0.8:
        summary['status'] = "PARTIAL_SUCCESS"
        summary['message'] = "Most DDL actions executed successfully"
    elif report.success_rate > 0:
        summary['status'] = "PARTIAL_FAILURE"
        summary['message'] = "Some DDL actions failed"
    else:
        summary['status'] = "FAILURE"
        summary['message'] = "All DDL actions failed"
    
    return summary


def log_execution_plan(ddl_plan: DdlPlan, dry_run: bool = True) -> None:
    """
    Log the DDL execution plan for audit purposes.
    
    Args:
        ddl_plan: DDL plan to log
        dry_run: Whether this is a dry run
    """
    mode = "[DRY RUN] " if dry_run else ""
    logger.info(f"{mode}DDL Execution Plan - {len(ddl_plan.actions)} actions")
    
    for i, action in enumerate(ddl_plan.actions, 1):
        safety = "SAFE" if action.safe else "UNSAFE"
        logger.info(f"{mode}  {i}. {action.action_type} on {action.table_name} ({safety})")
        
        if action.column_name:
            logger.info(f"{mode}     Column: {action.column_name}")
        
        if action.sql_type:
            logger.info(f"{mode}     Type: {action.sql_type}")
        
        if action.definition:
            logger.info(f"{mode}     Definition: {action.definition}")
    
    if ddl_plan.estimated_execution_time_seconds:
        logger.info(f"{mode}Estimated execution time: {ddl_plan.estimated_execution_time_seconds:.1f} seconds")
