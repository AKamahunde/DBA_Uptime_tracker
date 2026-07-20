@echo off
echo Starting DBA Tracker API on Waitress...
echo Make sure you have installed waitress (pip install waitress)
echo.

cd /d "%~dp0"
call venv\Scripts\activate.bat

echo Running Waitress on port 8080...
waitress-serve --port=8080 dba_tracker.wsgi:application
