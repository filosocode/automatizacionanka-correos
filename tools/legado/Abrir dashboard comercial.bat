@echo off
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" app.py mode | find "chatgpt_work" >nul
if not errorlevel 1 start "Puente Work" "%~dp0Puente Work - Dashboard.bat"
".venv\Scripts\python.exe" app.py review-ui
pause
