"""Registro de la aplicación: consola con todo el detalle, archivo sin datos de clientes.

Uso en cada módulo:

    from anka.infra.logging_setup import SENSITIVE, get_logger
    log = get_logger(__name__)
    log.info("%s: resultado", thread_id)                       # va a consola y a archivo
    log.info("%s: %s", thread_id, resumen, extra=SENSITIVE)    # solo a consola

Regla: el archivo (`logs/anka.log`) guarda identificadores y resultados, nunca contenido
de correos, destinatarios, actividades ni borradores. Lo que lleve datos de un cliente se
marca con `extra=SENSITIVE`. Como red de seguridad, el archivo además reemplaza cualquier
dirección de correo que se cuele (incluso en tracebacks).

`ANKA_LOG_LEVEL=DEBUG` sube el detalle de la consola y del archivo (sin datos sensibles).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from anka.infra import settings

ROOT_NAME = "anka"
LOG_FILE_NAME = "anka.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5
SENSITIVE = {"sensitive": True}

_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLER_TAG = "_anka_handler"


def get_logger(name: str) -> logging.Logger:
    """Logger del paquete: acepta `__name__` y lo cuelga de la raíz `anka`."""
    return logging.getLogger(name if name.startswith(f"{ROOT_NAME}.") else f"{ROOT_NAME}.{name}")


class _DropSensitive(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "sensitive", False)


class _RedactingFormatter(logging.Formatter):
    """Formatea y luego reemplaza correos, también dentro del traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return _EMAIL.sub("<correo>", super().format(record))


def _level_from_env() -> int:
    name = os.getenv("ANKA_LOG_LEVEL", "INFO").strip().upper()
    level = logging.getLevelNamesMapping().get(name)
    return level if isinstance(level, int) and level in (10, 20, 30, 40) else logging.INFO


def reset_logging() -> None:
    """Quita y cierra los handlers que puso `configure_logging` (idempotencia y tests)."""
    root = logging.getLogger(ROOT_NAME)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()


def configure_logging(
    log_dir: Path | None = None, console: bool = True, stream: TextIO | None = None,
) -> Path | None:
    """Configura consola y archivo rotativo. Devuelve la ruta del archivo (None si no hay).

    La consola escribe en `stdout` (como los `print` que reemplaza) salvo que se indique `stream`.

    Nunca lanza: si el archivo no se puede abrir, avisa por consola y sigue sin él,
    porque un problema de registro no debe detener el piloto.
    """
    reset_logging()
    root = logging.getLogger(ROOT_NAME)
    level = _level_from_env()
    root.setLevel(level)
    root.propagate = False
    if console:
        console_handler = logging.StreamHandler(stream or sys.stdout)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(console_handler, _HANDLER_TAG, True)
        root.addHandler(console_handler)
    directory = log_dir if log_dir is not None else settings.DATA_DIR / "logs"
    path = directory / LOG_FILE_NAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True,
        )
    except OSError as exc:
        root.warning("No se pudo abrir el archivo de registro %s: %s", path, exc)
        return None
    file_handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.addFilter(_DropSensitive())
    setattr(file_handler, _HANDLER_TAG, True)
    root.addHandler(file_handler)
    return path
