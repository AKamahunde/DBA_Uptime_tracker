@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python manage.py close_resolved_incidents
exit
