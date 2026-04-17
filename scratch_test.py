import os
from sqlalchemy import create_engine
from pipeline.csvdog import csvdog

def main():
    print("Initialize DB...")
    db_path = "sqlite:///test_csvdog.db"
    engine = create_engine(db_path)
    
    print("--- FIRST PASS (Should load all and infer PKs) ---")
    results_run_1 = csvdog("csv", engine, "my_csv_tracker.json")
    print(results_run_1)
    
    print("\n--- SECOND PASS (Should skip unchanged files) ---")
    results_run_2 = csvdog("csv", engine, "my_csv_tracker.json")
    print(results_run_2)

if __name__ == "__main__":
    main()
