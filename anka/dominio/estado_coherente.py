"""El estado de una actividad siempre cuenta lo mismo que sus datos y su nota (R12, U-104).

Ninguna actividad queda «Completa» si le falta un dato esencial o su nota dice que algo falta, y ninguna queda «Por
completar» si tiene todos los datos esenciales y su nota no deja nada pendiente. Las contradictorias no se tocan: son
cosa de una persona. Se aplica a lo que produce el análisis, en los tres modos, nunca a lo que una persona corrigió.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from anka.dominio.activities import normalize_estado
from anka.dominio.lo_que_falta import deja_dudas, dice_pendiente, lo_pendiente

DATOS_ESENCIALES = (("ubicacion", "ubicación"), ("quantity", "cantidad"), ("unit", "unidad"),
                    ("entregable", "entregable"), ("plazo_restriccion", "plazo"))
_VACIOS = ("", "no indicado", "no indicada", "n/a", "na", "-", "—", "sin dato", "sin datos", "por definir", "pendiente")


def datos_que_faltan(item: Mapping[str, Any]) -> list[str]:
    """Los datos esenciales de la fila (R12) vacíos o «no indicado»: «ubicación», «plazo»…"""
    return [nombre for clave, nombre in DATOS_ESENCIALES
            if " ".join(str(item.get(clave) or "").split()).casefold() in _VACIOS]


def _anotar(fila: MutableMapping[str, Any], texto: str) -> None:
    fila["notes"] = f"{str(fila.get('notes') or '').strip()} {texto}".strip()


def reconciliar_estados(filas: Sequence[Any]) -> list[str]:
    """Corrige el estado de las filas que no coinciden con sus datos y su nota; devuelve qué cambió, para la huella."""
    cambios: list[str] = []
    for fila in filas:
        if not isinstance(fila, dict) or not fila.get("activity"):
            continue
        estado = normalize_estado(fila.get("estado"))
        if estado == "contradictorio":
            continue
        faltan = datos_que_faltan(fila)
        nota = str(fila.get("notes") or "")
        nombre = str(fila["activity"])
        if estado == "confirmado" and (faltan or dice_pendiente(nota)):
            fila["estado"] = "pendiente"
            if not lo_pendiente(nota):
                _anotar(fila, f"Falta {', '.join(faltan)}.")
            cambios.append(f"«{nombre}» pasó a por completar: " + (f"falta {', '.join(faltan)}" if faltan
                                                                  else "su nota dice qué falta"))
        elif estado == "pendiente" and not faltan and not deja_dudas(nota):
            fila["estado"] = "confirmado"
            _anotar(fila, "Completa: tiene todos los datos esenciales y nada pendiente.")
            cambios.append(f"«{nombre}» pasó a completa: tiene todos los datos esenciales y nada pendiente")
    return cambios
