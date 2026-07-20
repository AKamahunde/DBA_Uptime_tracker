@echo off
echo ============================================================
echo  DBA Operations Uptime Tracker — Setup Runner
echo ============================================================
echo.

cd /d "%~dp0"

REM ── 1. Generate the improved Excel workbook ──────────────────────────────
echo [1/2] Generating Excel workbook...
python create_excel_template.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Python script failed. Trying venv...
    call backend\venv\Scripts\activate.bat 2>nul
    python create_excel_template.py
)

echo.

REM ── 2. Apply improved SQL views to MySQL ────────────────────────────────
echo [2/2] Applying SQL views to MySQL (dba_tracker)...
echo       Host: 192.168.35.38  User: root
echo.
mysql -h 192.168.35.38 -u root -pAdm1n@2020 dba_tracker < create_views_and_permissions.sql
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: mysql CLI not in PATH. 
    echo Please apply the SQL manually:
    echo   - Open MySQL Workbench or HeidiSQL
    echo   - Connect to 192.168.35.38 as root
    echo   - Run: create_views_and_permissions.sql
    echo.
    echo --- OR use the Python runner: ---
    echo   python run_views.py
)

echo.
echo ============================================================
echo  Done! Check above for any errors.
echo  Excel file: DBA Operations Uptime Tracker.xlsx
echo ============================================================
pause
