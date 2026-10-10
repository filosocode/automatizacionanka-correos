"""Desempeño del análisis medido con las correcciones del uso real (T-35).

Lógica pura. Cada corrección que una persona hace sobre lo que el análisis propuso mide su acierto: se compara
la tabla que dejó el análisis con la que dejó la persona, y lo que se le sugirió para las preguntas con lo que decidió.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from anka.dominio.activities import numero_de_cantidad, unidad_canonica

MINIMO_DE_CASOS = 10      # con menos casos en un grupo, las tasas no permiten concluir


def _plano(texto: object) -> str:
    descompuesto = unicodedata.normalize("NFD", str(texto or ""))
    return " ".join("".join(c for c in descompuesto if unicodedata.category(c) != "Mn").casefold().split())


def _cantidad(fila: Mapping[str, Any]) -> tuple[float | str | None, str]:
    """La cantidad de una fila como (número o texto, unidad unificada), para saber si cambió."""
    cantidad = fila.get("quantity")
    valor = numero_de_cantidad(cantidad)
    unidad = _plano(unidad_canonica(str(fila.get("unit") or "")))
    return (valor if valor is not None else _plano(cantidad) or None, unidad)


def cambios_de_tabla(inicial: Sequence[Mapping[str, Any]], final: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Qué cambió una persona de la tabla que dejó el análisis: filas quitadas, agregadas y campos corregidos.

    Las filas se emparejan por el nombre de la actividad (sin tildes ni mayúsculas), una con una.
    """
    disponibles: dict[str, list[Mapping[str, Any]]] = {}
    for fila in final:
        disponibles.setdefault(_plano(fila.get("activity")), []).append(fila)
    conservadas = cantidades = estados = ubicaciones = 0
    for fila in inicial:
        candidatas = disponibles.get(_plano(fila.get("activity")))
        if not candidatas:
            continue
        despues = candidatas.pop(0)
        conservadas += 1
        cantidades += _cantidad(fila) != _cantidad(despues)
        estados += _plano(fila.get("estado")) != _plano(despues.get("estado"))
        ubicaciones += _plano(fila.get("ubicacion")) != _plano(despues.get("ubicacion"))
    return {"iniciales": len(inicial), "finales": len(final), "conservadas": conservadas,
            "quitadas": len(inicial) - conservadas, "agregadas": len(final) - conservadas,
            "cantidades_cambiadas": cantidades, "estados_cambiados": estados, "ubicaciones_cambiadas": ubicaciones}


def concordancia_de_preguntas(preguntas: Sequence[Mapping[str, Any]] | None) -> dict[str, int]:
    """Cuánto coincidió la persona con lo que se le sugirió para cada pregunta, y cuántas agregó ella."""
    decididas = de_acuerdo = sacadas = pasadas = propias = 0
    for pregunta in preguntas or []:
        if pregunta.get("origen") == "persona":
            propias += 1
            continue
        sugerida, decision = pregunta.get("sugerida"), pregunta.get("decision")
        if not sugerida or not decision:
            continue
        decididas += 1
        if decision == sugerida:
            de_acuerdo += 1
        elif sugerida == "ask_client":
            sacadas += 1
        elif decision == "ask_client":
            pasadas += 1
    return {"decididas": decididas, "de_acuerdo": de_acuerdo, "sacadas_del_cliente": sacadas,
            "pasadas_al_cliente": pasadas, "propias": propias}


def _razon(parte: float, total: float) -> float | None:
    return round(parte / total, 3) if total else None


def resumir_grupo(casos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Las tasas de un grupo de casos (misma versión de prompt y modelo); None donde no hay nada que medir."""
    iniciales = sum(c["cambios"]["iniciales"] for c in casos)
    conservadas = sum(c["cambios"]["conservadas"] for c in casos)
    cambiadas = sum(c["cambios"]["cantidades_cambiadas"] for c in casos)
    return {
        "casos": len(casos),
        "pocos": len(casos) < MINIMO_DE_CASOS,
        "sin_correcciones": _razon(sum(not c["tuvo_edicion"] for c in casos), len(casos)),
        "filas_conservadas": _razon(conservadas, iniciales),
        "cantidades_intactas": _razon(conservadas - cambiadas, conservadas),
    }

