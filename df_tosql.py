import os
import json
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import create_engine, inspect, text

from utils.cleaner import quick_clean
from aligner.outliers import detect_outliers, apply_outlier_action
from utils.casting import cast_df
from utils.profiler import profile_dataframe, get_pk
from pipeline import run as pipeline_run

_LOG = logging.getLogger(__name__)

def load_engine_from_env() -> Any:
    url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if not url:
        raise ValueError("engine is None and DB_URL/DATABASE_URL not set in environment.")
    return create_engine(url)

def load_table_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            _LOG.warning(f"Failed to load table metrics JSON '{path}': {e}")
    return {}

def write_table_json(path: str, data: dict) -> None:
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _LOG.warning(f"Failed to write table metrics JSON '{path}': {e}")

def df_tosql(
    df: Any,
    table: str,
    engine: Optional[Any] = None,
    if_exist: str = "insert",
    dtype: Optional[Dict[str, str]] = None,
    schema: Optional[str] = None,
    chunk: int = 10000,
    table_constraints: Optional[Dict[str, Any]] = None,
    where: Optional[List[Any]] = None,
    expression: str = "",
    add_new_column: bool = True,
    clean: bool = True,
    cast: bool = True,
    auto_profiling: bool = False,
    outlier: float = 0.5,
    table_json: str = "table_metadata.json"
) -> Dict[str, Any]:
    """
    Main orchestration wrapper for DataFrame -> DB injection handling creation, cleaning, 
    and routing.
    """
    # 1. Normalize Inputs
    if isinstance(df, dict):
        work_df = pd.DataFrame([df])
    elif isinstance(df, list) and df and isinstance(df[0], dict):
        work_df = pd.DataFrame(df)
    elif isinstance(df, str):
        if df.endswith('.csv'):
            work_df = pd.read_csv(df)
        elif df.endswith('.parquet'):
            work_df = pd.read_parquet(df)
        elif df.endswith('.json'):
            work_df = pd.read_json(df)
        else:
            raise ValueError("Unsupported file format. Must be .csv, .parquet, or .json")
    else:
        if not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame, list of dicts, dict, or file path")
        work_df = df.copy()

    if work_df.empty:
        return {"table": table, "row_count": 0, "issues": ["Empty DataFrame"]}

    eng = engine if engine is not None else load_engine_from_env()

    # 2. Config Loading
    config = load_table_json(table_json)

    # 3. Clean & Cast
    if clean:
        work_df = quick_clean(work_df)
        if outlier:
            numeric_cols = [c for c in work_df.columns if pd.api.types.is_numeric_dtype(work_df[c])]
            if numeric_cols:
                try:
                    res = detect_outliers(work_df, numeric_cols, method="iqr", iqr_factor=1.5)
                    if res.outlier_rows > 0:
                        work_df, _ = apply_outlier_action(work_df, res, action="drop")
                except Exception as e:
                    _LOG.warning(f"Outlier detection failed: {e}")
    if cast:
        work_df = cast_df(work_df, dtype=dtype)
        
    # 4. Profiling constraints
    if auto_profiling and not table_constraints:
        profile = profile_dataframe(work_df)
        potential_pk = get_pk(profile)
        if potential_pk:
            table_constraints = {"pk": [potential_pk[0]]}
            
    constrain_args = table_constraints.get("pk") if table_constraints else None
    
    # Normalize mode mapping
    mode = if_exist.lower()
    if mode == "upsert" and not constrain_args:
        raise ValueError("if_exist='upsert' requires primary key columns in table_constraints['pk']")

    # 5. Table Creation / Reflection Rules
    table_created = False
    inspector = inspect(eng)
    db_schema = schema if schema else None
    
    if not inspector.has_table(table, schema=db_schema):
        # Use ddl_create for standalone engine-free CREATE TABLE statement capability
        from utils.ddl_create import df_ddl
        
        db_type = eng.dialect.name.lower()
        if "mssql" in db_type or "sqlserver" in db_type:
            db_type = "mssql"
            
        pk_for_ddl = constrain_args if constrain_args else None
        
        # ddl_create emits the CREATE TABLE and optional MSSQL partition pre_script
        ddl_str, meta, work_df = df_ddl(
            work_df, 
            table, 
            server=db_type, 
            schema=db_schema, 
            pk=pk_for_ddl
        )
        
        with eng.begin() as conn:
            conn.execute(text(ddl_str))
        table_created = True
        _LOG.info(f"Created new table '{table}'.")
    else:
        # Table exists. Honor add_new_column logic
        from aligner.reflect import reflect_table_spec
        spec = reflect_table_spec(eng, db_schema, table)
        known_cols = list(spec.columns.keys())
        
        if not add_new_column:
            # Warning and drop unknown
            missing = [c for c in work_df.columns if str(c).lower() not in [sc.lower() for sc in known_cols]]
            if missing:
                _LOG.warning(f"Dropping unknown columns because add_new_column=False: {missing}")
                work_df = work_df.drop(columns=missing)

    if mode == "replace":
        with eng.begin() as conn:
            scope = f"{db_schema}.{table}" if db_schema else table
            if eng.dialect.name.lower() == "sqlite":
                conn.execute(text(f"DELETE FROM {table}"))
            else:
                conn.execute(text(f"TRUNCATE TABLE {scope}"))
        mode = "insert"

    # 6. Alignment & Write Execution via core Pipeline
    from aligner.policies import AlignmentPolicies, DdlPolicy
    policies = AlignmentPolicies(
        ddl=DdlPolicy(
            enabled=True,
            allow_add_columns=add_new_column,
            allow_widen_columns=True, 
            allow_alter_type=False
        )
    )

    pipe_result = pipeline_run(
        engine=eng,
        df=work_df,
        table=table,
        schema=db_schema,
        mode=mode,
        constrain=constrain_args,
        where=where,
        expression=expression,
        policies=policies,
        apply_ddl=add_new_column,
        dry_run=False,
        chunk=chunk
    )
    rows_affected = pipe_result.get("result", 0)

    # 7. Persist Config
    config.update({
        "table": table,
        "schema": schema,
        "primary_keys": constrain_args,
        "dtypes": {k: str(v) for k, v in work_df.dtypes.to_dict().items()}
    })
    write_table_json(table_json, config)

    return {
        "table": table,
        "row_count": len(work_df),
        "rows_affected": rows_affected,
        "mode": if_exist,
        "table_created": table_created,
        "columns_added": add_new_column,
        "issues": []
    }