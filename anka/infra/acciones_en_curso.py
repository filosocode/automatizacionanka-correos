"""Acciones en curso: marcas de lo que el dashboard está haciendo, para que el lanzador no lo corte a la mitad (U-59).

El dashboard y el lanzador son procesos distintos. Mientras una acción de la persona se ejecuta (enviar un correo,
guardar la tabla, emitir la T), el dashboard deja un archivo en `en_curso/`; el lanzador, antes de reiniciar por un
cambio de código, espera hasta un tope a que no quede ninguno. Un archivo viejo (un proceso que murió) no cuenta.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from anka.infra import settings

CARPETA = "en_curso"
EDAD_MAXIMA = 300.0        # segundos: una marca más vieja es de una acción que murió con su proceso, no se espera
ESPERA_MAXIMA = 20.0       # segundos que el lanzador espera a que terminen las acciones antes de reiniciar


def _carpeta() -> Path:
    return settings.DATA_DIR / CARPETA


@contextmanager
def en_curso(nombre: str) -> Iterator[None]:
    """Marca que `nombre` se ejecuta mientras dure el bloque; si no se puede escribir la marca, sigue sin ella."""
    marca: Path | None = None
    try:
        _carpeta().mkdir(parents=True, exist_ok=True)
        marca = _carpeta() / f"{os.getpid()}-{threading.get_ident()}-{uuid4().hex[:8]}.marca"
        marca.write_text(nombre, encoding="utf-8")
    except OSError:
        marca = None
    try:
        yield
    finally:
        if marca is not None:
            marca.unlink(missing_ok=True)


def acciones_activas(ahora: Callable[[], float] = time.time) -> list[str]:
    """Los nombres de las acciones que siguen en curso (sin contar marcas viejas)."""
    activas = []
    try:
        marcas = list(_carpeta().glob("*.marca"))
    except OSError:
        return []
    for marca in marcas:
        try:
            if ahora() - marca.stat().st_mtime <= EDAD_MAXIMA:
                activas.append(marca.read_text(encoding="utf-8"))
        except OSError:
            continue        # terminó (y borró su marca) mientras se recorría
    return activas


def esperar_a_que_terminen(tope: float = ESPERA_MAXIMA, pausa: float = 0.5,
                           dormir: Callable[[float], None] = time.sleep,
                           ahora: Callable[[], float] = time.monotonic) -> bool:
    """Espera hasta `tope` segundos a que no quede ninguna acción en curso; devuelve si ya no queda ninguna."""
    limite = ahora() + tope
    while acciones_activas():
        if ahora() >= limite:
            return False
        dormir(pausa)
    return True
