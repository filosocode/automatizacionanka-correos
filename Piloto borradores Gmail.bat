@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" app.py mode
if errorlevel 1 goto fin
".venv\Scripts\python.exe" app.py run --apply --gmail-drafts --watch-seconds 300
:fin
pause
