@echo off
rem Mismas verificaciones que el CI: lint y tests con cobertura.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ruff check . || exit /b 1
".venv\Scripts\python.exe" -m coverage run -m unittest discover || exit /b 1
".venv\Scripts\python.exe" -m coverage report
