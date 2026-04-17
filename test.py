"""
test.py -- Batch-test all CSV files from a directory using the CSV Harness.

Scans the target directory, infers the Primary Key for each file,
and runs the full INSERT -> UPSERT -> UPDATE benchmark cycle.
"""
import time
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from pipeline.csv_harness import run_csv_pipeline
from utils.profiler import profile_dataframe, get_pk

# ── Configuration ──
CSV_DIR = Path("csv")
DB_URL = "sqlite:///test_harness.db"
engine = sa.create_engine(DB_URL)

# ── Discover CSVs ──
csv_files = sorted(CSV_DIR.glob("*.csv"))
print(f"Found {len(csv_files)} CSV files in '{CSV_DIR}'\n")

results = []

for i, csv_path in enumerate(csv_files, 1):
    table_name = csv_path.stem.lower().replace(" ", "_").replace("-", "_")
    print(f"[{i}/{len(csv_files)}] {csv_path.name} -> {table_name}")
    
    t0 = time.perf_counter()
    try:
        # Load, Clean, and Profile
        from utils.ddl import sanitize_dataframe_columns
        df = pd.read_csv(csv_path)
        
        # Print the first few rows of the csv as requested
        print("\n--- CSV PREVIEW ---")
        print(df.head(3).to_string())
        print("-------------------\n")
        
        df, _ = sanitize_dataframe_columns(df, server="sqlite", allow_space=False, to_lower=True)
        info = profile_dataframe(df)
        _, _, meta = get_pk(df, info)
        
        # Extract true column names
        pk_cols = meta.get("components", []) 
        
        # If no PK can be confidently inferred, fallback to the first column 
        if not pk_cols:
            pk_cols = [df.columns[0]]
            
        print(f"   Inferred PK: {pk_cols}")
        
        # The harness requires strictly unique PKs and >= 5 rows
        df = df.drop_duplicates(subset=pk_cols)
        if len(df) < 5:
            raise ValueError(f"Not enough rows for harness after dedup (got {len(df)})")
            
        # Ensure the table is created with the exact schema needed for the subset of data
        from config import ensure_table
        ensure_table(
            engine=engine, 
            df=df, 
            table=table_name, 
            if_exists="drop", 
            pk_cols=pk_cols
        )
        
        # Save temp deduplicated CSV for the harness
        temp_csv = csv_path.with_suffix('.tmp.csv')
        df.to_csv(temp_csv, index=False)
        
        try:
            from crud import CrudConfig
            report = run_csv_pipeline(
                csv_path=str(temp_csv),
                engine=engine,
                table=table_name,
                pk_cols=pk_cols,
                constraint_cols=pk_cols,
                config=CrudConfig(echo_sql=True)
            )
            
            elapsed = round(time.perf_counter() - t0, 2)
            
            # Safely print summary for Windows terminals (avoid cp1252 encode errors)
            summary_str = report.summary().encode("cp1252", errors="replace").decode("cp1252")
            print(summary_str)
            
            # Calculate full success
            all_ok = all(step.validation_passed for step in report.steps)
            results.append((csv_path.name, table_name, str(pk_cols), all_ok, elapsed, None))
        finally:
            if temp_csv.exists():
                temp_csv.unlink()
        
    except Exception as e:
        elapsed = round(time.perf_counter() - t0, 2)
        safe_err = str(e).encode("cp1252", errors="replace").decode("cp1252")
        print(f"   [FAIL] {safe_err}")
        results.append((csv_path.name, table_name, "N/A", False, elapsed, str(e)))

# ── Summary ──
print("\n" + "=" * 80)
print(f"{'FILE':<30} {'TABLE':<25} {'PK':<10} {'TIME':>6}  STATUS")
print("-" * 80)
for file, tbl, pk, ok, t, err in results:
    status = "OK" if ok else "FAIL"
    print(f"{file:<30} {tbl:<25} {pk:<10} {t:>5.1f}s  {status}")

passed = sum(1 for r in results if r[3])
failed = len(results) - passed
print("-" * 80)
print(f"Total: {passed} passed, {failed} failed out of {len(results)} files")
