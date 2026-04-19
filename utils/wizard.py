"""
Interactive CLI wizard for SqlSql.
Provides guided database setup and ETL configuration.
Replicates the functionality of the legacy sqlpen.bat in cross-platform Python.
"""
import os
import sys
import logging
from typing import Optional, Dict, Any
import sqlalchemy as sa

# --- ANSI Colors ---
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header(title: str):
    clear_screen()
    print(f"{CYAN}{BOLD}")
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print(f"{RESET}")

def get_input(prompt: str, default: Optional[str] = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    full_prompt = f"  {prompt}{suffix}: "
    
    if secret:
        import getpass
        val = getpass.getpass(full_prompt)
    else:
        val = input(full_prompt)
        
    return val.strip() or (default if default is not None else "")

def setup_database_wizard() -> Optional[str]:
    """Guided menu to build a SQLAlchemy URL and save to .env."""
    print_header("SqlSql -- Database Connection Setup")
    print("  Welcome! Let's configure your database connection.")
    print("  This will be saved to .env for future use.")
    print("")
    print(f"  {BOLD}What type of database are you connecting to?{RESET}")
    print("  " + "-" * 50)
    print("   [1] PostgreSQL")
    print("   [2] SQLite (local file)")
    print("   [3] Oracle")
    print("   [4] MySQL / MariaDB")
    print("   [5] SQL Server (MSSQL)")
    print("   [6] Paste a full connection URL")
    print("   [0] Cancel")
    print("  " + "-" * 50)
    
    choice = get_input("Choose [0-6]", "1")
    
    url = None
    if choice == "0":
        return None
    elif choice == "1": # Postgres
        u = get_input("Username", "postgres")
        p = get_input("Password", secret=True)
        h = get_input("Host", "localhost")
        port = get_input("Port", "5432")
        db = get_input("Database name")
        url = f"postgresql://{u}:{p}@{h}:{port}/{db}"
    elif choice == "2": # SQLite
        path = get_input("File path", "./test.db")
        # Normalize for SQLAlchemy
        path = path.replace("\\", "/")
        url = f"sqlite:///{path}"
    elif choice == "3": # Oracle
        u = get_input("Username")
        p = get_input("Password", secret=True)
        h = get_input("Host", "localhost")
        port = get_input("Port", "1521")
        sn = get_input("Service Name / SID", "XEPDB1")
        # Preference for oracledb driver
        url = f"oracle+oracledb://{u}:{p}@{h}:{port}/?service_name={sn}"
    elif choice == "4": # MySQL
        u = get_input("Username", "root")
        p = get_input("Password", secret=True)
        h = get_input("Host", "localhost")
        port = get_input("Port", "3306")
        db = get_input("Database name")
        url = f"mysql+pymysql://{u}:{p}@{h}:{port}/{db}"
    elif choice == "5": # MSSQL
        h = get_input("Host", "localhost")
        db = get_input("Database name")
        u = get_input("Username (leave blank for Windows Auth)", "")
        p = get_input("Password", secret=True) if u else ""
        drv = get_input("ODBC Driver", "ODBC Driver 17 for SQL Server")
        if u:
            url = f"mssql+pyodbc://{u}:{p}@{h}/{db}?driver={drv}"
        else:
            url = f"mssql+pyodbc://{h}/{db}?driver={drv}&trusted_connection=yes"
    elif choice == "6":
        url = get_input("Paste full URL")
        
    if not url:
        return None

    # Test connection
    print(f"\n  {CYAN}Testing connection...{RESET}")
    try:
        engine = sa.create_engine(url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        print(f"  {GREEN}Connection Successful!{RESET}")
    except Exception as e:
        print(f"  {RED}Connection Failed: {e}{RESET}")
        retry = get_input("Save anyway? (y/n)", "n")
        if retry.lower() != 'y':
            return None

    # Save to .env
    save = get_input("Save to .env?", "y")
    if save.lower() == 'y':
        _write_env(url)
        print(f"  {GREEN}Saved to .env{RESET}")
        
    return url

def _write_env(url: str):
    env_path = ".env"
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
            
    # Remove existing DATABASE_URL
    lines = [l for l in lines if not l.startswith("DATABASE_URL=")]
    lines.append(f"DATABASE_URL={url}\n")
    
    with open(env_path, "w") as f:
        f.writelines(lines)

def interactive_menu():
    """Main interactive menu logic."""
    while True:
        print_header("SqlSql -- Guided ETL Utility")
        
        # Load current DB
        db_url = os.environ.get("DATABASE_URL")
        if not db_url and os.path.exists(".env"):
            with open(".env", "r") as f:
                for line in f:
                    if line.startswith("DATABASE_URL="):
                        db_url = line.split("=", 1)[1].strip()
        
        if db_url:
            # Mask password for display
            masked = db_url
            if "@" in db_url and "://" in db_url:
                pre, post = db_url.split("@", 1)
                proto, auth = pre.split("://", 1)
                if ":" in auth:
                    user = auth.split(":", 1)[0]
                    masked = f"{proto}://{user}:****@{post}"
            print(f"  {GREEN}Active DB:{RESET} {masked}")
        else:
            print(f"  {YELLOW}Active DB:{RESET} None (use [7] to setup)")
            
        print("")
        print(f"  {BOLD}MAIN MENU{RESET}")
        print("  " + "-" * 50)
        print("   [1] Load a file (ETL)")
        print("   [2] Run DML test (Harness)")
        print("   [3] List tables")
        print("   [4] Describe table")
        print("   [5] Execute SQL query")
        print("   [7] Database connection setup")
        print("   [0] Exit")
        print("  " + "-" * 50)
        
        choice = get_input("Choose [0-7]", "0")
        
        if choice == "0":
            print(f"\n  {GREEN}Goodbye!{RESET}")
            sys.exit(0)
        elif choice == "7":
            setup_database_wizard()
        elif choice == "1":
            _menu_load(db_url)
        elif choice == "3":
            _menu_list_tables(db_url)
        # Add more mappings as implemented
        else:
            print(f"\n  {YELLOW}Not implemented yet in wizard.{RESET}")
            get_input("Press Enter to continue")

def _menu_load(db_url: Optional[str]):
    if not db_url:
        print(f"  {RED}Error: Setup database first!{RESET}")
        get_input("Press Enter")
        return
        
    print_header("Load File into Database")
    src = get_input("Source file path (CSV/Parquet/etc.)")
    if not os.path.exists(src):
        print(f"  {RED}Error: File not found!{RESET}")
        get_input("Press Enter")
        return
        
    tbl = get_input("Target table name")
    mode = get_input("Mode (insert/upsert/replace)", "insert")
    pk = ""
    if mode == "upsert":
        pk = get_input("Primary key columns (comma-separated)")

    confirm = get_input(f"Run {mode} for {src} into {tbl}? (y/n)", "y")
    if confirm.lower() == 'y':
        # We would ideally call the CLI command or internal API here
        print(f"\n  {CYAN}Executing...{RESET}")
        from df_tosql import df_tosql
        try:
            # Simple wrapper for now
            import pandas as pd
            if src.endswith('.csv'): df = pd.read_csv(src)
            elif src.endswith('.parquet'): df = pd.read_parquet(src)
            else: raise ValueError("Extension not supported in simple wizard")
            
            engine = sa.create_engine(db_url)
            kwargs = {
                "engine": engine,
                "df": df,
                "table": tbl,
                "schema": None,
                "if_exist": mode,
            }
            if pk:
                kwargs["table_constraints"] = {"pk": [k.strip() for k in pk.split(",")]}
            
            df_tosql(**kwargs)
            print(f"\n  {GREEN}Success!{RESET}")
        except Exception as e:
            print(f"\n  {RED}Failed: {e}{RESET}")
        get_input("Press Enter to continue")

def _menu_list_tables(db_url: Optional[str]):
    if not db_url: return
    print_header("Database Tables")
    try:
        engine = sa.create_engine(db_url)
        insp = sa.inspect(engine)
        tables = insp.get_table_names()
        for t in sorted(tables):
            print(f"  - {t}")
    except Exception as e:
        print(f"  {RED}Failed: {e}{RESET}")
    get_input("\n  Press Enter to continue")
