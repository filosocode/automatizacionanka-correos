@echo off
rem Mismas verificaciones que el CI: lint, tipos y tests con cobertura.
rem
rem   check.bat          todo, con cobertura: lo que decide si algo se puede dar por hecho (igual que el CI)
rem   check.bat quick    ruff, mypy, check_spec y las pruebas rapidas (sin cobertura, sin los modulos lentos de
rem                      tools\pruebas_rapidas.py): para revisar mientras se trabaja, nunca para cerrar un cambio
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ruff check . || exit /b 1
".venv\Scripts\python.exe" -m mypy || exit /b 1
".venv\Scripts\python.exe" check_spec.py || exit /b 1
if /i "%~1"=="quick" (
    ".venv\Scripts\python.exe" tools\pruebas_rapidas.py --rapido || exit /b 1
    echo.
    echo Modo rapido: faltan las pruebas lentas y la cobertura. Antes de cerrar el cambio corre check.bat sin argumentos.
    exit /b 0
)
".venv\Scripts\python.exe" -m coverage run -m unittest discover -s tests -t . || exit /b 1
".venv\Scripts\python.exe" -m coverage report
