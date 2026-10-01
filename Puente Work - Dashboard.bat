@echo off
cd /d "%~dp0"
title Puente Work - Dashboard (no cerrar)
:inicio
".venv\Scripts\python.exe" work_dashboard_sync.py --seconds 60
if errorlevel 2 goto fin
echo.
echo Puente detenido %date% %time%. Reiniciando en 15 s (Ctrl+C para salir)...
timeout /t 15 /nobreak >nul
goto inicio
:fin
echo.
echo El puente no se reinicia solo: revisa el mensaje de arriba.
pause
