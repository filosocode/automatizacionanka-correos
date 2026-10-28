"""Instancia única entre procesos con un candado del sistema operativo (U-81, D-151).

El candado es un byte reservado del archivo (`msvcrt` en Windows, `flock` en POSIX): lo libera el sistema si el proceso
muere, así que no hay bloqueos obsoletos que adivinar ni una carrera entre comprobar y tomar. La información del archivo
(PID, identificador de instancia, hora) solo sirve para explicar quién lo tiene: nunca decide, y cada instancia borra
únicamente la suya.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from anka.dominio.errores import ErrorDeAnka

LOCK_OFFSET = 4096    # byte reservado: en Windows el bloqueo es obligatorio, así la información de arriba sigue legible
TAMANO_INFORMACION = 1024
_BINARIO = getattr(os, "O_BINARY", 0)


class OtraInstanciaActiva(ErrorDeAnka):
    """Ya hay otra instancia con el candado; el texto dice cuál (PID y hora) sin mostrar nada más."""


def _tomar(descriptor: int) -> None:
    """Toma el candado sin esperar; `OSError` si lo tiene otro proceso (o este mismo con otro descriptor)."""
    if sys.platform == "win32":
        import msvcrt
        os.lseek(descriptor, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _soltar(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(descriptor, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _interpretar(crudo: bytes) -> dict[str, Any] | None:
    try:
        valor = json.loads(crudo.replace(b"\0", b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return valor if isinstance(valor, dict) and valor else None


def leer_informacion(path: Path) -> dict[str, Any] | None:
    """La información que dejó la instancia que tiene (o tuvo) el candado; `None` si no hay o no se puede leer."""
    try:
        with path.open("rb", buffering=0) as archivo:     # sin búfer: leer de más cruzaría el byte bloqueado
            return _interpretar(archivo.read(TAMANO_INFORMACION))
    except OSError:
        return None


def _leer(descriptor: int) -> dict[str, Any] | None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    return _interpretar(os.read(descriptor, TAMANO_INFORMACION))


def _escribir(descriptor: int, informacion: dict[str, Any] | None) -> None:
    """Un solo `write` de tamaño fijo (relleno con NUL): un lector nunca ve un archivo a medias."""
    crudo = json.dumps(informacion, separators=(",", ":")).encode("utf-8") if informacion else b""
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, crudo[:TAMANO_INFORMACION].ljust(TAMANO_INFORMACION, b"\0"))


class InstanciaUnica:
    """Candado de instancia única; se usa con `with` o con `adquirir()` y `liberar()`."""

    def __init__(self, path: Path, nombre: str = "proceso") -> None:
        self.path = path
        self.nombre = nombre
        self.token = uuid.uuid4().hex
        self.recuperada_de: dict[str, Any] | None = None   # información de una instancia anterior que ya no existe
        self._descriptor: int | None = None

    def adquirir(self) -> InstanciaUnica:
        """Toma el candado o lanza `OtraInstanciaActiva`; si había información de una instancia muerta, la anota."""
        if self._descriptor is not None:
            raise RuntimeError("Este candado ya está tomado por esta instancia.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | _BINARIO)
        try:
            _tomar(descriptor)
        except OSError:
            otra = leer_informacion(self.path) or {}
            os.close(descriptor)
            conocida = otra.get("pid") and otra.get("iniciado")
            quien = f" (PID {otra['pid']}, desde {otra['iniciado']})" if conocida else ""
            raise OtraInstanciaActiva(f"Ya hay un {self.nombre} activo en este equipo{quien}.") from None
        try:
            previa = _leer(descriptor)
            if previa and previa.get("token") != self.token:
                self.recuperada_de = previa     # el sistema nos dio el candado: esa instancia ya no existe
            _escribir(descriptor, {"nombre": self.nombre, "pid": os.getpid(), "token": self.token,
                                   "iniciado": datetime.now().astimezone().isoformat(timespec="seconds")})
        except BaseException:
            _soltar(descriptor)
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def liberar(self) -> None:
        """Suelta solo el candado propio y borra únicamente la información propia."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            actual = _leer(descriptor)
            if actual and actual.get("token") == self.token:
                _escribir(descriptor, None)
        finally:
            try:
                _soltar(descriptor)
            finally:
                os.close(descriptor)

    def __enter__(self) -> InstanciaUnica:
        return self.adquirir()

    def __exit__(self, *_: object) -> None:
        self.liberar()
