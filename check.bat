@echo off
rem Mismas verificaciones que el CI: lint, tipos y tests con cobertura.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ruff check . || exit /b 1
".venv\Scripts\python.exe" -m mypy || exit /b 1
".venv\Scripts\python.exe" check_spec.py || exit /b 1
".venv\Scripts\python.exe" -m coverage run -m unittest discover -s tests -t . || exit /b 1
".venv\Scripts\python.exe" -m coverage report
