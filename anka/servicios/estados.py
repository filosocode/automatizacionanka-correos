"""Cambios de estado de un caso comprobados contra `spec/transiciones.json`: una no prevista se registra (U-46).

Primera etapa (D-111): nunca bloquea. Si el cambio no está en la tabla, el caso cambia igual y queda un hecho
`transicion_no_prevista` en su historial y una línea en el registro, con caso, origen, destino y evento.
"""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4
from datetime import datetime

from anka.infra.logging_setup import get_logger
from anka.infra.store import record_event
from anka.servicios.repositorio import actualizar_caso

log = get_logger(__name__)

RUTA_TABLA = Path(__file__).resolve().parents[2] / "spec" / "transiciones.json"


@lru_cache(maxsize=1)
def _tabla() -> tuple[tuple[str, frozenset[str], str], ...]:
    """Las filas como (evento, orígenes, destino); vacía si el archivo no está (entonces no se comprueba)."""
    try:
        filas = json.loads(RUTA_TABLA.read_text(encoding="utf-8"))["transiciones"]
    except (OSError, ValueError, KeyError):
        log.warning("No se pudo leer spec/transiciones.json; no se comprueban las transiciones")
        return ()
    return tuple((str(f["evento"]), frozenset(f["desde"]), str(f["hacia"])) for f in filas)


def prevista(evento: str, desde: str, hacia: str) -> bool:
    """¿La tabla contempla este cambio para este evento? Quedarse igual nunca es un cambio."""
    if desde == hacia:
        return True
    tabla = _tabla()
    if not tabla:
        return True
    return any(e == evento and desde in origenes and destino == hacia for e, origenes, destino in tabla)


def verificar_transicion(db: sqlite3.Connection, thread_id: str, hacia: str, evento: str) -> bool:
    """Compara el estado actual del caso con el destino; si no está en la tabla lo registra. True si estaba prevista.

    No cambia el estado ni bloquea nada. Un caso que todavía no existe no tiene transición.
    """
    fila = db.execute("SELECT status FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
    if not fila:
        return True
    desde = str(fila["status"])
    hacia = str(hacia)
    if prevista(evento, desde, hacia):
        return True
    log.warning("%s: transición no prevista %s -> %s (evento %s)", thread_id, desde, hacia, evento)
    ahora = datetime.now().astimezone().isoformat(timespec="seconds")
    record_event(db, thread_id, "transicion_no_prevista", "sistema", f"{ahora}-{uuid4().hex[:6]}",
                 f"Cambio de estado que la tabla no prevé: de «{desde}» a «{hacia}» (evento {evento}). Se permitió.",
                 ahora)
    return False


def cambiar_estado(db: sqlite3.Connection, thread_id: str, hacia: str, evento: str, **campos: Any) -> bool:
    """Cambia el estado del caso (y los demás campos permitidos) registrando si la tabla no lo prevé."""
    previsto = verificar_transicion(db, thread_id, hacia, evento)
    actualizar_caso(db, thread_id, status=hacia, **campos)
    return previsto
