"""Escritura completa y privada de archivos sensibles (token de Google, token CSRF): temporal + `os.replace` (U-44)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from anka.infra.logging_setup import get_logger

log = get_logger(__name__)


def _restringir(path: Path) -> bool:
    """Deja el archivo legible solo por el usuario actual. True si se pudo."""
    try:
        if os.name == "nt":
            usuario = os.environ.get("USERNAME", "")
            if not usuario:
                return False
            quitar_herencia = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{usuario}:(R,W,D)"],
                capture_output=True, timeout=15, check=False)
            return quitar_herencia.returncode == 0
        os.chmod(path, 0o600)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def escribir_privado(path: Path, contenido: str) -> None:
    """Escribe `contenido` en `path` sin que nunca quede a medias y con permiso solo del usuario.

    Va a un temporal de la misma carpeta (con el permiso ya restringido) y se mueve con `os.replace`. Si no se puede
    restringir el permiso se avisa en el registro (sin el contenido) y se guarda igual: perder el token es peor.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, nombre = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporal = Path(nombre)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        if not _restringir(temporal):
            log.warning("No se pudo restringir el permiso de %s al usuario actual", path.name)
        os.replace(temporal, path)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
