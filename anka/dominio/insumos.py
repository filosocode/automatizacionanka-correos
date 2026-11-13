"""Las preguntas al cliente agrupadas por el insumo que las resuelve (U-114).

Lógica pura, sin modelo, sin base de datos y sin Gmail (D-209). Recibe las preguntas que se van a poner en el correo y
las actividades del caso, y devuelve las mismas preguntas sin repeticiones: las que piden solo el archivo de ubicación
se reúnen en una línea que nombra las actividades que cubre, las de plazo de varias actividades en una pregunta del
proyecto y la pregunta general de magnitud se quita cuando cada actividad ya pregunta la suya. Las contradictorias, los
entregables y lo que solo una actividad necesita no se tocan nunca (R12).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from anka.dominio.activities import normalize_estado
from anka.dominio.estado_coherente import datos_que_faltan
from anka.dominio.evaluacion import temas_preguntados
from anka.dominio.preguntas import PREFIJO_UBICACION, huella_de_pregunta, pide_el_archivo_de_ubicacion

SALIDA_UBICACION = "Si no lo tiene, díganos el municipio y las coordenadas aproximadas."
PREGUNTA_DE_PLAZO = "¿Para cuándo necesita el proyecto y hay fechas distintas por actividad?"
_ANEXO = re.compile(r"\banexos?\b", re.IGNORECASE)


class _Pregunta(NamedTuple):
    texto: str
    actividad: str | None      # la actividad de la tabla de la que sale; None si es general
    clase: str                 # ubicacion, plazo, magnitud, otra


def _con_y(partes: Sequence[str]) -> str:
    """«a», «a y b», «a, b y c»."""
    return partes[0] if len(partes) == 1 else f"{', '.join(partes[:-1])} y {partes[-1]}"


def _actividad_de(pregunta: str, nombres: Sequence[str]) -> tuple[str | None, str]:
    """(la actividad que nombra al principio, el resto de la pregunta): «Batimetría: la ubicación.»"""
    huella = huella_de_pregunta(pregunta)
    elegido = max((n for n in nombres if (h := huella_de_pregunta(n)) and huella.startswith(h)),
                  key=lambda n: len(huella_de_pregunta(n)), default=None)
    if elegido is None:
        return None, pregunta
    propio = pregunta.casefold().startswith(elegido.casefold())
    resto = pregunta[len(elegido):] if propio else pregunta.partition(":")[2]
    return elegido, resto.lstrip(" :-–—")


def _clase(resto: str) -> str:
    """De qué trata una pregunta: solo el archivo de ubicación, solo el plazo, solo una magnitud u otra cosa."""
    if pide_el_archivo_de_ubicacion(resto):
        return "ubicacion"
    temas = temas_preguntados([resto])
    return next(iter(temas)) if temas in ({"plazo"}, {"magnitud"}) else "otra"


def _clasificar(preguntas: Sequence[str], actividades: Sequence[Mapping[str, Any]]) -> list[_Pregunta]:
    por_nombre = {str(a.get("activity") or "").strip(): a for a in actividades if a.get("activity")}
    resultado = []
    for texto in preguntas:
        nombre, resto = _actividad_de(texto, list(por_nombre))
        contradictoria = nombre is not None and normalize_estado(por_nombre[nombre].get("estado")) == "contradictorio"
        resultado.append(_Pregunta(texto, nombre, "otra" if contradictoria else _clase(resto)))
    return resultado


def linea_de_ubicacion(nombres: Sequence[str]) -> str:
    """La línea que pide un solo archivo de ubicación para varias actividades, con su salida si no lo tiene."""
    return f"{PREFIJO_UBICACION}: {_con_y(nombres)}. {SALIDA_UBICACION}"


def agrupar_preguntas(preguntas: Sequence[str], actividades: Sequence[Mapping[str, Any]]) -> list[str]:
    """Las `preguntas` sin repeticiones, en el orden en que aparecen (U-114).

    El anexo citado va primero: puede aclarar varias de las demás."""
    items = _clasificar(preguntas, [a for a in actividades if isinstance(a, Mapping)])
    quitar: set[int] = set()
    ubicacion = [i for i, p in enumerate(items) if p.clase == "ubicacion"]
    if len(ubicacion) >= 2:
        nombres = list(dict.fromkeys(a for i in ubicacion if (a := items[i].actividad)))
        if nombres:
            items[ubicacion[0]] = _Pregunta(linea_de_ubicacion(nombres), None, "ubicacion")
        quitar.update(ubicacion[1:])
    del_plazo = [i for i, p in enumerate(items) if p.clase == "plazo" and p.actividad]
    if len(del_plazo) >= 2:
        items[del_plazo[0]] = _Pregunta(PREGUNTA_DE_PLAZO, None, "plazo")
        quitar.update(del_plazo[1:])
        quitar.update(i for i, p in enumerate(items)       # la general sobra; la del grupo (`del_plazo[0]`) se queda
                      if p.clase == "plazo" and not p.actividad and i != del_plazo[0])
    if _cada_actividad_pregunta_su_magnitud(items, actividades):
        quitar.update(i for i, p in enumerate(items) if p.clase == "magnitud" and not p.actividad)
    quedan = [p for i, p in enumerate(items) if i not in quitar]
    anexos = [p for p in quedan if not p.actividad and _ANEXO.search(p.texto)]
    return [p.texto for p in [*anexos, *(p for p in quedan if p not in anexos)]]


def _cada_actividad_pregunta_su_magnitud(items: Sequence[_Pregunta],
                                         actividades: Sequence[Mapping[str, Any]]) -> bool:
    """¿Cada actividad sin cantidad o unidad pregunta la suya? Solo entonces sobra la pregunta general."""
    con_magnitud = {p.actividad for p in items if p.clase == "magnitud" and p.actividad}
    if not con_magnitud:
        return False
    sin_magnitud = [str(a.get("activity") or "").strip() for a in actividades
                    if normalize_estado(a.get("estado")) != "confirmado"
                    and {"cantidad", "unidad"} & set(datos_que_faltan(a))]
    return all(nombre in con_magnitud for nombre in sin_magnitud)


def cubierta_en_el_correo(pregunta: str, actividades: Sequence[Mapping[str, Any]], puntos: Sequence[str]) -> bool:
    """¿Alguna línea del correo ya hace esta pregunta de una actividad, sola o dentro de un grupo? (U-114.3)"""
    huellas = [huella_de_pregunta(p) for p in puntos]
    if huella_de_pregunta(pregunta) in huellas:
        return True
    nombres = [str(a.get("activity") or "").strip() for a in actividades if a.get("activity")]
    nombre, _ = _actividad_de(pregunta, nombres)
    if nombre is None:
        return False
    clase = _clasificar([pregunta], actividades)[0].clase
    if clase == "ubicacion":
        inicio = huella_de_pregunta(PREFIJO_UBICACION)
        return any(h.startswith(inicio) and huella_de_pregunta(nombre) in h for h in huellas)
    return clase == "plazo" and huella_de_pregunta(PREGUNTA_DE_PLAZO) in huellas
