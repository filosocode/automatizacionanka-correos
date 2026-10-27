"""Las áreas que el cliente escribe en el correo, contrastadas con las que el programa calculó del KML/KMZ (T-126)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from anka.dominio.analisis import AreaCalculada, DiferenciaDeArea
from anka.dominio.areas_calculadas import areas_de_los_adjuntos

TOLERANCIA_DE_AREA = 0.05   # lo medido contra ofertas reales difiere hasta ~2 %; un 5 % ya merece una mirada
MAX_POLIGONOS_A_CONTRASTAR = 200   # con cientos de polígonos casi cualquier cifra «coincide» con alguno por azar
MAX_DIFERENCIAS = 10
_METROS_POR_UNIDAD = {"ha": 1e4, "has": 1e4, "hectarea": 1e4, "hectareas": 1e4,
                      "m2": 1.0, "km2": 1e6}
_AREA_ESCRITA = re.compile(
    r"(?<![\w.,])(?P<numero>\d+(?:[.,]\d+)*)\s*(?P<unidad>hect[aá]reas?|has?|km\s?2|km²|m\s?2|m²)(?![\w²])",
    re.IGNORECASE)


def _lecturas_posibles(numero: str) -> list[float]:
    """Todas las lecturas de un número escrito en español: «2.677» puede ser 2,677 o 2677; «0,8» solo 0,8."""
    partes = re.split(r"[.,]", numero)
    if len(partes) == 1:
        return [float(numero)]
    lecturas: list[float] = []
    if len(partes) == 2:
        lecturas.append(float(f"{partes[0]}.{partes[1]}"))
        if len(partes[1]) == 3:
            lecturas.append(float(partes[0] + partes[1]))
        return lecturas
    con_decimales = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+)[.,](\d+)", numero)
    if con_decimales:
        lecturas.append(float(f"{re.sub(r'[.,]', '', con_decimales.group(1))}.{con_decimales.group(2)}"))
    elif all(len(p) == 3 for p in partes[1:]):
        lecturas.append(float("".join(partes)))
    return lecturas


def _unidad(escrita: str) -> str:
    sin_espacios = re.sub(r"\s", "", escrita.lower()).replace("²", "2").replace("á", "a")
    return "ha" if sin_espacios.startswith(("ha", "hect")) else sin_espacios


def areas_declaradas(texto: str) -> list[tuple[str, list[float]]]:
    """Las áreas escritas en un texto: (cómo se escribió, sus lecturas posibles en hectáreas), sin repetir."""
    vistas: set[tuple[float, ...]] = set()
    encontradas: list[tuple[str, list[float]]] = []
    for m in _AREA_ESCRITA.finditer(texto):
        metros = _METROS_POR_UNIDAD.get(_unidad(m["unidad"]))
        lecturas = _lecturas_posibles(m["numero"])
        if not metros or not lecturas:
            continue
        hectareas = [round(v * metros / 1e4, 6) for v in lecturas]
        if tuple(hectareas) in vistas:
            continue
        vistas.add(tuple(hectareas))
        encontradas.append((f"{m['numero']} {m['unidad']}", hectareas))
    return encontradas


def pistas_de_area(correo: str, adjuntos: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Las dos pistas de área de un hilo: las calculadas de sus KML/KMZ y lo que el correo dice distinto."""
    areas = areas_de_los_adjuntos(adjuntos)
    return {"areas_calculadas": areas, "diferencias_de_area": diferencias_de_area(correo, areas)}


def _coincide(declarada: float, calculada: float) -> bool:
    return calculada > 0 and abs(declarada - calculada) <= TOLERANCIA_DE_AREA * max(declarada, calculada)


def diferencias_de_area(correo: str, areas: Sequence[AreaCalculada]) -> list[DiferenciaDeArea]:
    """Las áreas del correo que ningún polígono de los archivos respalda, ni solo ni sumados.

    Es una pista: el cliente pudo hablar de otra cosa que sus polígonos. Sin polígonos, o con demasiados para que la
    comparación signifique algo, no dice nada. Nunca corrige ni elige entre las dos cifras.
    """
    if not areas or len(areas) > MAX_POLIGONOS_A_CONTRASTAR:
        return []
    calculadas = [float(a["area_ha"]) for a in areas]
    total = sum(calculadas)
    diferencias: list[DiferenciaDeArea] = []
    for escrita, lecturas in areas_declaradas(correo):
        if any(_coincide(d, c) for d in lecturas for c in (*calculadas, total)):
            continue
        declarada = lecturas[0]
        indice = min(range(len(areas)),
                     key=lambda i: min(abs(math.log(max(d, 1e-9) / max(calculadas[i], 1e-9))) for d in lecturas))
        diferencias.append({"escrita": escrita, "declarada_ha": declarada, "poligono": areas[indice]["poligono"],
                            "archivo": areas[indice]["archivo"], "calculada_ha": calculadas[indice]})
    return diferencias[:MAX_DIFERENCIAS]
