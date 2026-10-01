@echo off
cd /d "%~dp0"
schtasks /Create /TN "Anka" /SC ONLOGON /RL LIMITED /F /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0supervisor.py\""
if errorlevel 1 (echo No se pudo crear la tarea.) else (echo Listo: Anka arrancara solo al iniciar sesion en Windows. Para quitarlo: Quitar autoarranque.bat)
pause
