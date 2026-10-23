"""Aísla el laboratorio de los datos reales de ANKA (T-118). Se importa antes que cualquier módulo de `anka.infra`.

Los lectores de `anka/infra/` consultan `settings.DATA_DIR` (config, base, evidencia). El laboratorio los apunta a una carpeta
temporal que se borra al terminar, para que ninguna lectura toque `%LOCALAPPDATA%\\AnkaCorreos` (credenciales, casos, base).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEMPORAL = tempfile.mkdtemp(prefix="anka-lab-evidencia-")
os.environ["ANKA_DATA_DIR"] = _TEMPORAL
atexit.register(shutil.rmtree, _TEMPORAL, ignore_errors=True)


def carpeta_temporal() -> Path:
    """La carpeta de datos que usan los lectores durante esta ejecución."""
    return Path(_TEMPORAL).resolve()


def comprobar() -> None:
    """Falla si los lectores quedaron apuntando a otra carpeta (p. ej. porque `anka.infra.settings` se importó antes)."""
    from anka.infra import settings

    if Path(settings.DATA_DIR).resolve() != carpeta_temporal():
        raise RuntimeError("Los lectores no están aislados: importa laboratorio.evidencia_documental.aislamiento antes que anka.")
