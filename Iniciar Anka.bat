@echo off
cd /d "%~dp0"
title Anka - dashboard y puente (no cerrar)
echo Anka: dashboard en http://127.0.0.1:8765/  (Ctrl+C para detener todo)
start "" http://127.0.0.1:8765/
".venv\Scripts\python.exe" supervisor.py
pause
