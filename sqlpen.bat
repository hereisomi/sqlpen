@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  SqlPen Interactive Launcher
::  A guided menu for using SqlPen without knowing any commands
:: ============================================================

set "SQLPEN_DIR=%~dp0"
:: Remove trailing backslash for PYTHONPATH
if "%SQLPEN_DIR:~-1%"=="\" set "SQLPEN_DIR=%SQLPEN_DIR:~0,-1%"
set "PYTHONPATH=%SQLPEN_DIR%\..;%PYTHONPATH%"
set "SQLPEN=python -m SqlPen"
set "VERSION=0.1.0"
set "ENV_FILE=%~dp0.env"
set "SAVED_URL="

:: ---- Colours (works on Windows 10+) -----------------------
for /f %%A in ('echo prompt $E^| cmd') do set "ESC=%%A"
set "RESET=%ESC%[0m"
set "CYAN=%ESC%[96m"
set "GREEN=%ESC%[92m"
set "YELLOW=%ESC%[93m"
set "RED=%ESC%[91m"
set "BOLD=%ESC%[1m"
set "DIM=%ESC%[2m"

:: ---- Load existing DATABASE_URL from .env if present ------
call :LOAD_ENV

:: ---- First-run check: no connection configured? -----------
if "!SAVED_URL!"=="" (
    call :FIRST_RUN_SETUP
)

goto :MAIN_MENU


:: ============================================================
:LOAD_ENV
:: Reads DATABASE_URL=... from .env file into SAVED_URL
set "SAVED_URL="
if not exist "%ENV_FILE%" goto :eof
for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
    if /i "%%A"=="DATABASE_URL" set "SAVED_URL=%%B"
)
goto :eof


:: ============================================================
:SAVE_ENV
:: Writes DATABASE_URL=!SAVED_URL! to .env (preserves other keys)
set "TMP_ENV=!ENV_FILE!.tmp"
if exist "!ENV_FILE!" (
    :: Copy all non-DATABASE_URL lines
    type nul > "!TMP_ENV!"
    for /f "usebackq tokens=* delims=" %%L in ("!ENV_FILE!") do (
        set "LINE=%%L"
        if /i not "!LINE:~0,13!"=="DATABASE_URL=" (
            echo(!LINE!>>"!TMP_ENV!"
        )
    )
    echo DATABASE_URL=!SAVED_URL!>>"!TMP_ENV!"
    move /y "!TMP_ENV!" "!ENV_FILE!" >nul
) else (
    echo DATABASE_URL=!SAVED_URL!>"!ENV_FILE!"
)
goto :eof


:: ============================================================
:FIRST_RUN_SETUP
cls
echo %CYAN%
echo  =========================================================
echo    SqlPen -- First Time Setup
echo  =========================================================
echo %RESET%
echo  Welcome! No database connection found.
echo  Let's set one up. It will be saved to .env so you
echo  %GREEN%never need to enter it again.%RESET%
echo.
echo  %DIM%Location: %ENV_FILE%%RESET%
echo.
echo  %BOLD%What type of database are you connecting to?%RESET%
echo  ---------------------------------------------------------
echo   [1]  PostgreSQL       (most common for production)
echo   [2]  SQLite           (local file, great for testing)
echo   [3]  MySQL / MariaDB
echo   [4]  Oracle
echo   [5]  SQL Server (MSSQL)
echo   [6]  I already have a full connection URL, let me paste it
echo   [0]  Skip for now
echo  ---------------------------------------------------------
set /p DB_TYPE="  Choose database type [0-6]: "

if "!DB_TYPE!"=="0" goto :eof
if "!DB_TYPE!"=="1" goto :SETUP_POSTGRES
if "!DB_TYPE!"=="2" goto :SETUP_SQLITE
if "!DB_TYPE!"=="3" goto :SETUP_MYSQL
if "!DB_TYPE!"=="4" goto :SETUP_ORACLE
if "!DB_TYPE!"=="5" goto :SETUP_MSSQL
if "!DB_TYPE!"=="6" goto :SETUP_PASTE
echo  %RED%Invalid choice.%RESET%
timeout /t 2 >nul
goto :FIRST_RUN_SETUP


:SETUP_POSTGRES
cls
echo %CYAN%  -- PostgreSQL Connection Setup --%RESET%
echo.
echo  %YELLOW%Tip:%RESET% Default PostgreSQL port is 5432.
echo  %YELLOW%Tip:%RESET% You need a database created first. Ask your DBA if unsure.
echo.
set /p PG_USER="  Username [e.g. postgres]: "
set /p PG_PASS="  Password: "
set /p PG_HOST="  Host [default=localhost]: "
if "!PG_HOST!"=="" set "PG_HOST=localhost"
set /p PG_PORT="  Port [default=5432]: "
if "!PG_PORT!"=="" set "PG_PORT=5432"
set /p PG_DB="  Database name [e.g. mydb]: "
set "SAVED_URL=postgresql://!PG_USER!:!PG_PASS!@!PG_HOST!:!PG_PORT!/!PG_DB!"
goto :SETUP_CONFIRM


:SETUP_SQLITE
cls
echo %CYAN%  -- SQLite Connection Setup --%RESET%
echo.
echo  SQLite stores your database as a single file on disk.
echo  %YELLOW%Tip:%RESET% The file will be created automatically if it does not exist.
echo  %YELLOW%Tip:%RESET% Use a full path like C:\data\mydb.db or a relative path like .\mydb.db
echo.
set /p SQ_FILE="  Database file path [e.g. .\mydb.db]: "
if "!SQ_FILE!"=="" set "SQ_FILE=.\sqlpen.db"
:: Convert backslashes for SQLAlchemy URL
set "SQ_URL=!SQ_FILE:\=/!"
set "SAVED_URL=sqlite:///!SQ_URL!"
goto :SETUP_CONFIRM


:SETUP_MYSQL
cls
echo %CYAN%  -- MySQL / MariaDB Connection Setup --%RESET%
echo.
echo  %YELLOW%Tip:%RESET% Requires pymysql installed: pip install pymysql
echo  %YELLOW%Tip:%RESET% Default MySQL port is 3306.
echo.
set /p MY_USER="  Username [e.g. root]: "
set /p MY_PASS="  Password: "
set /p MY_HOST="  Host [default=localhost]: "
if "!MY_HOST!"=="" set "MY_HOST=localhost"
set /p MY_PORT="  Port [default=3306]: "
if "!MY_PORT!"=="" set "MY_PORT=3306"
set /p MY_DB="  Database name: "
set "SAVED_URL=mysql+pymysql://!MY_USER!:!MY_PASS!@!MY_HOST!:!MY_PORT!/!MY_DB!"
goto :SETUP_CONFIRM


:SETUP_ORACLE
cls
echo %CYAN%  -- Oracle Connection Setup --%RESET%
echo.
echo  %YELLOW%Tip:%RESET% Requires cx_Oracle: pip install cx_Oracle
echo  %YELLOW%Tip:%RESET% Default Oracle port is 1521. SID is usually ORCL or XE.
echo.
set /p OR_USER="  Username: "
set /p OR_PASS="  Password: "
set /p OR_HOST="  Host [default=localhost]: "
if "!OR_HOST!"=="" set "OR_HOST=localhost"
set /p OR_PORT="  Port [default=1521]: "
if "!OR_PORT!"=="" set "OR_PORT=1521"
set /p OR_SID="  Service name or SID [e.g. XE or ORCL]: "
if "!OR_SID!"=="" set "OR_SID=XE"
set "SAVED_URL=oracle+cx_oracle://!OR_USER!:!OR_PASS!@!OR_HOST!:!OR_PORT!/!OR_SID!"
goto :SETUP_CONFIRM


:SETUP_MSSQL
cls
echo %CYAN%  -- SQL Server (MSSQL) Connection Setup --%RESET%
echo.
echo  %YELLOW%Tip:%RESET% Requires pyodbc: pip install pyodbc
echo  %YELLOW%Tip:%RESET% Common driver name: "ODBC Driver 17 for SQL Server"
echo.
set /p MS_USER="  Username (blank = Windows auth): "
set /p MS_PASS="  Password (blank = Windows auth): "
set /p MS_HOST="  Server\Instance [e.g. localhost or SERVER\SQLEXPRESS]: "
set /p MS_DB="  Database name: "
set /p MS_DRV="  ODBC Driver [default=ODBC Driver 17 for SQL Server]: "
if "!MS_DRV!"=="" set "MS_DRV=ODBC Driver 17 for SQL Server"
if "!MS_USER!"=="" (
    set "SAVED_URL=mssql+pyodbc://!MS_HOST!/!MS_DB!?driver=!MS_DRV!&trusted_connection=yes"
) else (
    set "SAVED_URL=mssql+pyodbc://!MS_USER!:!MS_PASS!@!MS_HOST!/!MS_DB!?driver=!MS_DRV!"
)
goto :SETUP_CONFIRM


:SETUP_PASTE
cls
echo %CYAN%  -- Paste Connection URL --%RESET%
echo.
echo  Paste your full SQLAlchemy-style database URL:
echo  %DIM%Examples:%RESET%
echo   postgresql://user:pass@localhost:5432/mydb
echo   sqlite:///C:/data/mydb.db
echo   mysql+pymysql://user:pass@localhost/mydb
echo   oracle+cx_oracle://user:pass@localhost:1521/XE
echo   mssql+pyodbc://user:pass@server/db?driver=ODBC+Driver+17+for+SQL+Server
echo.
set /p SAVED_URL="  Connection URL: "
if "!SAVED_URL!"=="" (
    echo %RED%  URL cannot be empty.%RESET%
    timeout /t 2 >nul
    goto :SETUP_PASTE
)
goto :SETUP_CONFIRM


:SETUP_CONFIRM
cls
echo %CYAN%  -- Review Connection --%RESET%
echo.
echo  %BOLD%Connection URL:%RESET%
echo  %GREEN%!SAVED_URL!%RESET%
echo.
echo  %YELLOW%Step 1:%RESET% Test this connection before saving?
set /p DO_TEST="  Test connection now? [Y/n, default=Y]: "
if /i not "!DO_TEST!"=="N" (
    echo.
    echo  %CYAN%Testing connection...%RESET%
    %SQLPEN% tables --url "!SAVED_URL!" >nul 2>&1
    if errorlevel 1 (
        echo  %RED%  Connection FAILED.%RESET%
        echo  Check your credentials, host name, and ensure the database is reachable.
        echo.
        echo   [R]  Re-enter details
        echo   [S]  Save anyway
        echo   [0]  Skip and go to main menu without saving
        set /p RETRY="  Choose [R/S/0]: "
        if /i "!RETRY!"=="R" goto :FIRST_RUN_SETUP
        if "!RETRY!"=="0" goto :eof
    ) else (
        echo  %GREEN%  Connection OK!%RESET%
    )
)

echo.
echo  %YELLOW%Step 2:%RESET% Save this connection to %ENV_FILE%?
echo  %DIM%You won't be asked again — just launch the script and go.%RESET%
set /p DO_SAVE="  Save? [Y/n, default=Y]: "
if /i not "!DO_SAVE!"=="N" (
    call :SAVE_ENV
    echo.
    echo  %GREEN%  Saved! DATABASE_URL written to .env%RESET%
    timeout /t 2 >nul
) else (
    echo.
    echo  %YELLOW%  Not saved. You will be asked again next launch.%RESET%
    set "SAVED_URL="
    timeout /t 2 >nul
)
goto :eof


:: ============================================================
:HEADER
cls
echo %CYAN%
echo  =========================================================
echo    SqlPen Interactive Launcher  v%VERSION%
echo    Zero-Config ETL ^& DML Test Harness for Pandas to SQL
echo  =========================================================
echo %RESET%
if not "!SAVED_URL!"=="" (
    echo  %GREEN%DB:%RESET% %SAVED_URL%
) else (
    echo  %YELLOW%DB:%RESET% No connection saved. Use [7] Config to set one.
)
echo  %DIM%Tip: Run from the folder that contains your CSV files.%RESET%
echo.
goto :eof


:: ============================================================
:MAIN_MENU
call :HEADER
echo  %BOLD%MAIN MENU%RESET%
echo  ---------------------------------------------------------
echo   [1]  Load a file into database        ^(load^)
echo   [2]  Test harness - INSERT/UPSERT/UPDATE  ^(test^)
echo   [3]  Run batch jobs from config.yml   ^(run^)
echo   [4]  Execute a SQL query              ^(query^)
echo   [5]  List all tables in database      ^(tables^)
echo   [6]  Describe a table schema          ^(describe^)
echo   [7]  Manage config.yml                ^(config^)
echo   [8]  Show SqlPen version
echo   [0]  Exit
echo  ---------------------------------------------------------
set /p CHOICE="  Choose an option [0-8]: "

if "!CHOICE!"=="1" goto :LOAD_MENU
if "!CHOICE!"=="2" goto :TEST_MENU
if "!CHOICE!"=="3" goto :RUN_MENU
if "!CHOICE!"=="4" goto :QUERY_MENU
if "!CHOICE!"=="5" goto :TABLES_MENU
if "!CHOICE!"=="6" goto :DESCRIBE_MENU
if "!CHOICE!"=="7" goto :CONFIG_MENU
if "!CHOICE!"=="8" goto :VERSION_CMD
if "!CHOICE!"=="0" goto :EXIT
echo  %RED%Invalid choice. Please try again.%RESET%
timeout /t 2 >nul
goto :MAIN_MENU


:: ============================================================
:LOAD_MENU
call :HEADER
echo  %BOLD%LOAD FILE INTO DATABASE%RESET%
echo  ---------------------------------------------------------
echo  This loads a CSV, Excel, Parquet, or JSON file into a
echo  database table. Columns are auto-cleaned and types are
echo  auto-detected.
echo  ---------------------------------------------------------
echo.

:: Source file
echo  %YELLOW%Step 1:%RESET% Enter the path to your source file.
echo  Examples: data\users.csv   data\orders.xlsx   report.parquet
set /p SRC="  Source file: "
if "!SRC!"=="" (
    echo %RED%  Source file cannot be empty.%RESET% & timeout /t 2 >nul & goto :LOAD_MENU
)

:: Table
echo.
echo  %YELLOW%Step 2:%RESET% Enter the database table name to load into.
echo  Example: users   cdr_raw   telecom_events
set /p TBL="  Table name: "
if "!TBL!"=="" (
    echo %RED%  Table name cannot be empty.%RESET% & timeout /t 2 >nul & goto :LOAD_MENU
)

:: Database URL
echo.
echo  %YELLOW%Step 3:%RESET% Enter database URL, or leave blank to use DATABASE_URL from .env
echo  Postgres:   postgresql://user:password@localhost:5432/mydb
echo  SQLite:     sqlite:///mydb.db
echo  MySQL:      mysql+pymysql://user:password@localhost/mydb
echo  Oracle:     oracle+cx_oracle://user:password@localhost/XE
echo  SQL Server: mssql+pyodbc://user:password@server/db?driver=ODBC+Driver+17+for+SQL+Server
set /p DB_URL="  Database URL (blank = use .env): "

:: Write mode
echo.
echo  %YELLOW%Step 4:%RESET% How should existing data be handled?
echo   [1] insert  - Append new rows (default)
echo   [2] replace - Drop table and recreate with new data
echo   [3] upsert  - Insert new rows, update existing ones (needs constraint)
echo   [4] update  - Update existing rows only (needs constraint)
set /p MODE_CHOICE="  Mode [1-4, default=1]: "
if "!MODE_CHOICE!"=="2" (set "MODE=replace") else if "!MODE_CHOICE!"=="3" (set "MODE=upsert") else if "!MODE_CHOICE!"=="4" (set "MODE=update") else (set "MODE=insert")

:: Constraint (for upsert/update)
set "CONSTRAINT_ARG="
if "!MODE!"=="upsert" (
    echo.
    echo  %YELLOW%Step 5:%RESET% Enter the unique key column^(s^) for upsert matching.
    echo  Example: id   email   msisdn,date
    set /p CNSTR="  Constraint column(s): "
    if not "!CNSTR!"=="" set "CONSTRAINT_ARG=--constraint !CNSTR!"
)
if "!MODE!"=="update" (
    echo.
    echo  %YELLOW%Step 5:%RESET% Enter the WHERE key column^(s^) for update matching.
    set /p CNSTR="  Constraint column(s): "
    if not "!CNSTR!"=="" set "CONSTRAINT_ARG=--constraint !CNSTR!"
)

:: Build command
set "CMD=%SQLPEN% load "%SRC%" --table %TBL% --if-exist %MODE% %CONSTRAINT_ARG%"
if not "!DB_URL!"=="" set "CMD=%CMD% --url "%DB_URL%""

echo.
echo  %CYAN%Running:%RESET% %CMD%
echo.
%SQLPEN% load "%SRC%" --table %TBL% --if-exist %MODE% %CONSTRAINT_ARG% %DB_URL_ARG%

echo.
echo  %GREEN%Done!%RESET% Press any key to return to the main menu.
pause >nul
goto :MAIN_MENU


:: ============================================================
:TEST_MENU
call :HEADER
echo  %BOLD%DML TEST HARNESS%RESET%
echo  ---------------------------------------------------------
echo  The harness runs a full INSERT, UPSERT and UPDATE cycle
echo  against your database with your CSV data and reports
echo  what passed and what failed with root cause analysis.
echo.
echo  A report file ^(<csv_name^>_harness.txt^) is saved next
echo  to your CSV with all SQL queries and diagnostics.
echo  ---------------------------------------------------------
echo.

echo  %YELLOW%Step 1:%RESET% Enter path to your CSV file.
set /p SRC="  CSV file: "
if "!SRC!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :TEST_MENU)

echo.
echo  %YELLOW%Step 2:%RESET% Target table name in the database.
set /p TBL="  Table name: "
if "!TBL!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :TEST_MENU)

echo.
echo  %YELLOW%Step 3:%RESET% Primary key column(s) (used to verify rows exist).
echo  Example: id   subscriber_id   msisdn
set /p PK="  PK column(s) [default=id]: "
if "!PK!"=="" set "PK=id"

echo.
echo  %YELLOW%Step 4:%RESET% Unique constraint column(s) for UPSERT matching.
echo  Usually the same as PK. Example: id   email   msisdn
set /p CNSTR="  Constraint column(s) [default=same as PK]: "
if "!CNSTR!"=="" set "CNSTR=%PK%"

echo.
echo  %YELLOW%Step 5:%RESET% Database URL (blank = use .env)
set /p DB_URL="  Database URL: "

echo.
echo  %YELLOW%Step 6:%RESET% Run with full data pipeline?
echo   [Y] Yes (clean column names, auto-cast types, remove outliers) - recommended
echo   [N] No  (test raw data as-is against the database)
set /p PIPELINE="  Use full pipeline? [Y/n, default=Y]: "
set "NO_CLEAN_ARG="
set "NO_CAST_ARG="
if /i "!PIPELINE!"=="N" (
    set "NO_CLEAN_ARG=--no-clean"
    set "NO_CAST_ARG=--no-cast"
)

echo.
set "DB_URL_ARG="
if not "!DB_URL!"=="" set "DB_URL_ARG=--url "%DB_URL%""
echo  %CYAN%Running harness...%RESET%
echo.
%SQLPEN% test "%SRC%" --table %TBL% --pk %PK% --constraint %CNSTR% %NO_CLEAN_ARG% %NO_CAST_ARG% %DB_URL_ARG%

echo.
echo  %GREEN%Harness complete!%RESET%
echo  %YELLOW%Tip:%RESET% Check the _harness.txt report file next to your CSV for details.
pause >nul
goto :MAIN_MENU


:: ============================================================
:RUN_MENU
call :HEADER
echo  %BOLD%RUN BATCH JOBS%RESET%
echo  ---------------------------------------------------------
echo  Executes all jobs defined in config.yml automatically.
echo  Use "Manage config.yml" to add jobs first.
echo  ---------------------------------------------------------
echo.
echo  %YELLOW%Step 1:%RESET% Path to config.yml (blank = auto-detect in current folder)
set /p CFG="  Config file [blank=auto]: "
set "CFG_ARG="
if not "!CFG!"=="" set "CFG_ARG=--config "%CFG%""

echo.
echo  %CYAN%Running jobs from config.yml...%RESET%
echo.
%SQLPEN% run %CFG_ARG%

echo.
pause >nul
goto :MAIN_MENU


:: ============================================================
:QUERY_MENU
call :HEADER
echo  %BOLD%EXECUTE SQL QUERY%RESET%
echo  ---------------------------------------------------------
echo  Run any SQL and see results in the terminal or export
echo  them to a CSV or JSON file.
echo  ---------------------------------------------------------
echo.

echo  %YELLOW%Step 1:%RESET% Enter your SQL query.
echo  Example: SELECT * FROM users LIMIT 10
set /p SQL="  SQL: "
if "!SQL!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :QUERY_MENU)

echo.
echo  %YELLOW%Step 2:%RESET% Max rows to display on screen [default=20]
set /p LIMIT="  Row limit [20]: "
if "!LIMIT!"=="" set "LIMIT=20"

echo.
echo  %YELLOW%Step 3:%RESET% Export to file? Enter a path (.csv or .json) or leave blank.
echo  Example: output\results.csv   results.json
set /p OUTFILE="  Output file [blank=screen only]: "
set "OUT_ARG="
if not "!OUTFILE!"=="" set "OUT_ARG=--output "%OUTFILE%""

echo.
echo  %YELLOW%Step 4:%RESET% Database URL (blank = use .env)
set /p DB_URL="  Database URL: "
set "DB_URL_ARG="
if not "!DB_URL!"=="" set "DB_URL_ARG=--url "%DB_URL%""

echo.
echo  %CYAN%Running query...%RESET%
echo.
%SQLPEN% query "%SQL%" --limit %LIMIT% %OUT_ARG% %DB_URL_ARG%

echo.
pause >nul
goto :MAIN_MENU


:: ============================================================
:TABLES_MENU
call :HEADER
echo  %BOLD%LIST DATABASE TABLES%RESET%
echo  ---------------------------------------------------------
echo.

echo  %YELLOW%Step 1:%RESET% Database URL (blank = use .env)
set /p DB_URL="  Database URL: "
set "DB_URL_ARG="
if not "!DB_URL!"=="" set "DB_URL_ARG=--url "%DB_URL%""

echo.
echo  %YELLOW%Step 2:%RESET% Database schema (optional, for Postgres/Oracle/MSSQL)
echo  Example: public   dbo   HR   blank for default
set /p SCHEMA="  Schema [blank=default]: "
set "SCHEMA_ARG="
if not "!SCHEMA!"=="" set "SCHEMA_ARG=--schema %SCHEMA%"

echo.
echo  %CYAN%Fetching tables...%RESET%
echo.
%SQLPEN% tables %DB_URL_ARG% %SCHEMA_ARG%

echo.
pause >nul
goto :MAIN_MENU


:: ============================================================
:DESCRIBE_MENU
call :HEADER
echo  %BOLD%DESCRIBE TABLE SCHEMA%RESET%
echo  ---------------------------------------------------------
echo  Shows column names, types, nullable flags, and
echo  primary key / unique constraint information.
echo  ---------------------------------------------------------
echo.

echo  %YELLOW%Step 1:%RESET% Table name to inspect.
set /p TBL="  Table name: "
if "!TBL!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :DESCRIBE_MENU)

echo.
echo  %YELLOW%Step 2:%RESET% Show full details (PKs, unique constraints)?
set /p FULL="  Full details? [Y/n, default=Y]: "
set "FULL_ARG=--full"
if /i "!FULL!"=="N" set "FULL_ARG="

echo.
echo  %YELLOW%Step 3:%RESET% Database URL (blank = use .env)
set /p DB_URL="  Database URL: "
set "DB_URL_ARG="
if not "!DB_URL!"=="" set "DB_URL_ARG=--url "%DB_URL%""

echo.
echo  %CYAN%Describing table '%TBL%'...%RESET%
echo.
%SQLPEN% describe %TBL% %FULL_ARG% %DB_URL_ARG%

echo.
pause >nul
goto :MAIN_MENU


:: ============================================================
:CONFIG_MENU
call :HEADER
echo  %BOLD%MANAGE CONFIG.YML%RESET%
echo  ---------------------------------------------------------
echo   [1]  Show full config
echo   [2]  Show a specific setting
echo   [3]  Change a setting value
echo   [4]  Add a job to the jobs list
echo   [5]  Clear all jobs
echo   [6]  Create a fresh config.yml with defaults
echo   [0]  Back to main menu
echo  ---------------------------------------------------------
set /p CCHOICE="  Choose [0-6]: "

if "!CCHOICE!"=="0" goto :MAIN_MENU
if "!CCHOICE!"=="1" goto :CONFIG_SHOW_ALL
if "!CCHOICE!"=="2" goto :CONFIG_SHOW_KEY
if "!CCHOICE!"=="3" goto :CONFIG_SET
if "!CCHOICE!"=="4" goto :CONFIG_ADD_JOB
if "!CCHOICE!"=="5" goto :CONFIG_CLEAR_JOBS
if "!CCHOICE!"=="6" goto :CONFIG_INIT
echo  %RED%Invalid choice.%RESET% & timeout /t 2 >nul & goto :CONFIG_MENU


:CONFIG_SHOW_ALL
echo.
%SQLPEN% config show
echo.
pause >nul
goto :CONFIG_MENU


:CONFIG_SHOW_KEY
echo.
echo  %YELLOW%Enter the config key to view (use dot notation)%RESET%
echo  Examples: pipeline.chunk_size   pipeline.trace_sql   jobs
set /p KEY="  Key: "
echo.
%SQLPEN% config show %KEY%
echo.
pause >nul
goto :CONFIG_MENU


:CONFIG_SET
echo.
echo  %YELLOW%Available settings (dot notation):%RESET%
echo.
echo   pipeline.chunk_size          Default: 10000
echo   pipeline.outlier_pct         Default: 0.5
echo   pipeline.casting             Default: true
echo   pipeline.cleaner             Default: true
echo   pipeline.profiler            Default: true
echo   pipeline.trace_sql           Default: false
echo   schema_corrector.add_missing_cols   Default: false
echo   schema_corrector.on_error    Default: coerce
echo   schema_corrector.failure_threshold  Default: 3.0
echo   database_url                 Your database connection string
echo.
set /p KEY="  Setting to change: "
if "!KEY!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :CONFIG_MENU)
set /p VAL="  New value: "
if "!VAL!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :CONFIG_MENU)
echo.
%SQLPEN% config set %KEY% %VAL%
echo.
pause >nul
goto :CONFIG_MENU


:CONFIG_ADD_JOB
echo.
echo  %YELLOW%Add a new job to config.yml jobs list:%RESET%
echo  Jobs are run in sequence when you use "sqlpen run".
echo.
set /p SRC="  Source file path: "
if "!SRC!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :CONFIG_MENU)
set /p TBL="  Target table name: "
if "!TBL!"=="" (echo %RED%  Required.%RESET% & timeout /t 2 >nul & goto :CONFIG_MENU)
echo.
echo   [1] insert  [2] replace  [3] upsert  [4] update
set /p MODE_CHOICE="  Write mode [default=1 insert]: "
if "!MODE_CHOICE!"=="2" (set "MODE=replace") else if "!MODE_CHOICE!"=="3" (set "MODE=upsert") else if "!MODE_CHOICE!"=="4" (set "MODE=update") else (set "MODE=insert")
set /p CNSTR="  Constraint column(s) (optional, needed for upsert): "
set "CNSTR_ARG="
if not "!CNSTR!"=="" set "CNSTR_ARG=--constraint %CNSTR%"
echo.
%SQLPEN% config add-job --source "%SRC%" --table %TBL% --if-exist %MODE% %CNSTR_ARG%
echo.
pause >nul
goto :CONFIG_MENU


:CONFIG_CLEAR_JOBS
echo.
echo  %RED%Warning:%RESET% This will remove ALL jobs from config.yml.
%SQLPEN% config clear-jobs
echo.
pause >nul
goto :CONFIG_MENU


:CONFIG_INIT
echo.
echo  %YELLOW%This will create a fresh config.yml in the current folder.%RESET%
echo  Current folder: %CD%
set /p FORCE="  Overwrite existing? [y/N, default=N]: "
set "FORCE_ARG="
if /i "!FORCE!"=="Y" set "FORCE_ARG=--force"
echo.
%SQLPEN% config init %FORCE_ARG%
echo.
pause >nul
goto :CONFIG_MENU


:: ============================================================
:VERSION_CMD
call :HEADER
%SQLPEN% --version
echo.
pause >nul
goto :MAIN_MENU


:: ============================================================
:EXIT
cls
echo  %GREEN%Goodbye! Happy loading.%RESET%
echo.
endlocal
exit /b 0
