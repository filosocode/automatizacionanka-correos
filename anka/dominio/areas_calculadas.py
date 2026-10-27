"""Las áreas de los polígonos de un KML/KMZ: cómo se escriben en el texto y cómo se leen de vuelta (T-125)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from anka.dominio.analisis import AreaCalculada

AVISO_DE_DATO = "Dato calculado por el programa, no declarado por el cliente"
_LINEA_DE_AREA = re.compile(
    r"^Polígono «(?P<nombre>.*)»: área geodésica aproximada calculada (?P<m2>[\d.]+) m² \((?P<ha>[\d.]+) ha\); "
    r"perímetro (?P<perimetro>[\d.]+) m; anillos interiores: (?P<interiores>\d+)\.", re.MULTILINE)


def linea_de_area(etiqueta: str, resultado: Mapping[str, float | int], aviso: str) -> str:
    """La línea de texto de un polígono con área; `aviso` es la advertencia de `area_del_poligono`, si la hay."""
    area = float(resultado["area_m2"])
    perimetro = float(resultado["perimetro_m"])
    return (f"Polígono «{etiqueta}»: área geodésica aproximada calculada {area:.1f} m² ({area / 10000:.4f} ha); "
            f"perímetro {perimetro:.1f} m; anillos interiores: {resultado['anillos_interiores']}. "
            f"{AVISO_DE_DATO}{f'; {aviso}' if aviso else ''}")


def linea_sin_area(etiqueta: str, motivo: str) -> str:
    """La línea de texto de un polígono que no tiene área, con la razón."""
    return f"Polígono «{etiqueta}»: sin área calculada ({motivo})"


def lineas_del_total(areas_m2: list[float]) -> list[str]:
    """El área total del archivo cuando tiene más de un polígono con área."""
    if len(areas_m2) < 2:
        return []
    total = sum(areas_m2)
    return [f"Área total calculada de {len(areas_m2)} polígonos: {total:.1f} m² ({total / 10000:.4f} ha). "
            f"{AVISO_DE_DATO}"]


def areas_calculadas_del_texto(archivo: str, texto: str) -> list[AreaCalculada]:
    """Las áreas que `kml_text_file` dejó escritas en el texto de un archivo, listas para la pantalla."""
    return [{"archivo": archivo, "poligono": m["nombre"], "area_m2": float(m["m2"]), "area_ha": float(m["ha"]),
             "perimetro_m": float(m["perimetro"]), "anillos_interiores": int(m["interiores"])}
            for m in _LINEA_DE_AREA.finditer(texto)]


def areas_de_los_adjuntos(adjuntos: Iterable[Mapping[str, Any]]) -> list[AreaCalculada]:
    """Las áreas calculadas de todos los adjuntos leídos (cada uno con `filename` y `text`)."""
    areas: list[AreaCalculada] = []
    for adjunto in adjuntos:
        if isinstance(adjunto, Mapping) and "Polígono «" in str(adjunto.get("text") or ""):
            areas.extend(areas_calculadas_del_texto(str(adjunto.get("filename") or ""), str(adjunto["text"])))
    return areas
