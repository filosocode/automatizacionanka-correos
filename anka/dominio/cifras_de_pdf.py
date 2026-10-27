"""Las cifras de alcance que trae un PDF y que ninguna actividad de la tabla menciona (T-131).

Solo señala: lee el texto que el programa ya extrajo de cada PDF (nativo y OCR) y busca números con una unidad de
alcance («41,5 ha», «12 días»). No sabe qué servicio es cada una ni si es del cliente, así que no las agrega a la tabla.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from anka.dominio.analisis import CifraDePdf

MAX_CIFRAS = 10   # una pista corta: pasadas las diez, lo que sobra es ruido de un documento largo
_ANCHO_DE_CONTEXTO = 80
_PRIORIDAD = {"ha": 0, "km²": 0, "m²": 0, "km": 1, "m": 1, "ml": 1, "puntos": 2, "horas": 3, "días": 3}
_UNIDADES = {
    "ha": "ha", "hectarea": "ha", "hectareas": "ha", "km2": "km²", "km²": "km²", "km": "km", "kilometro": "km",
    "kilometros": "km", "m2": "m²", "m²": "m²", "ml": "ml", "metro": "m", "metros": "m", "hora": "horas",
    "horas": "horas", "dia": "días", "dias": "días", "punto": "puntos", "puntos": "puntos", "estaciones": "puntos",
    "sondeos": "puntos", "apiques": "puntos", "perforaciones": "puntos", "vertices": "puntos", "hitos": "puntos",
    "mojones": "puntos", "secciones": "puntos"}
_CIFRA = re.compile(
    r"(?<![\w.,/-])(?P<n>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,3}(?: \d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*"
    r"(?P<u>metros\s+cuadrados|[a-zA-ZáéíóúÁÉÍÓÚ²]+)(?![\w²/:])", re.IGNORECASE)
_RUIDO = re.compile(
    r"p\s*á\s*g\s*i\s*n\s*a|\bnorma\b|\biso \d|resoluci[oó]n \d|decreto|\bley \d|garant[ií]a|\bmm\b|\bcm\b|km/h"
    r"|certificad|expedici|expide|c[aá]mara de comercio",
    re.IGNORECASE)


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))


def _lecturas(numero: str) -> set[float]:
    """Los valores que puede significar un número escrito: «10,015» es 10,015 o 10 015; se admiten las dos."""
    limpio = numero.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", limpio):
        return {float(limpio.replace(".", "").replace(",", "."))}
    if re.fullmatch(r"\d{1,3},\d{3}", limpio):
        return {float(limpio.replace(",", ".")), float(limpio.replace(",", ""))}
    try:
        return {float(limpio.replace(",", "."))}
    except ValueError:   # dos números pegados por el texto de una tabla: no es una cifra
        return set()


def _unidad(texto: str) -> str | None:
    return _UNIDADES.get(_sin_tildes(" ".join(texto.split())).replace("metros cuadrados", "m2"))


def _numeros_de_la_tabla(actividades: Sequence[Mapping[str, Any]]) -> set[float]:
    texto = " ".join(str(v) for a in actividades if isinstance(a, Mapping) for v in a.values())
    return {v for m in re.finditer(r"\d[\d.,\s]*\d|\d", texto) for v in _lecturas(m.group().strip())}


def _mencionada(lecturas: set[float], unidad: str, en_tabla: set[float]) -> bool:
    equivalentes = set(lecturas)
    if unidad == "ha":
        equivalentes |= {v * 10000 for v in lecturas}
    elif unidad == "m²":
        equivalentes |= {v / 10000 for v in lecturas}
    return any(abs(v - t) < 1e-6 for v in equivalentes for t in en_tabla)


def cifras_de_alcance(
    archivo: str, texto: str, vistas: list[tuple[set[float], str]] | None = None,
) -> list[CifraDePdf]:
    """Los números con unidad de alcance de un texto, sin repetir y sin el ruido (normas, páginas, equipos).

    `vistas` son las cifras ya tomadas de otros archivos del hilo: la misma cifra no se repite
    (se comparan las lecturas: «10,015 m²» y «10.015 m²» son lo mismo).
    """
    vistas = [] if vistas is None else vistas
    cifras: list[CifraDePdf] = []
    for m in _CIFRA.finditer(texto):
        unidad = _unidad(m["u"])
        contexto = " ".join(texto[max(0, m.start() - _ANCHO_DE_CONTEXTO):m.end() + 50].split())
        alrededor = texto[max(0, m.start() - 3 * _ANCHO_DE_CONTEXTO):m.end() + 100]   # el ruido se reconoce más lejos
        lecturas = _lecturas(m["n"])
        if not unidad or not lecturas or min(lecturas) <= 0 or _RUIDO.search(alrededor):
            continue
        if not any(u == unidad and lecturas & otras for otras, u in vistas):
            vistas.append((lecturas, unidad))
            cifras.append({"archivo": archivo, "cifra": f"{m['n']} {unidad}", "contexto": contexto,
                           "lecturas": sorted(lecturas), "unidad": unidad})
    return cifras


def cifras_aun_sin_mencionar(
    cifras: Iterable[Mapping[str, Any]], actividades: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Las cifras guardadas como pista que la tabla de ahora todavía no menciona (la persona pudo escribirlas)."""
    en_tabla = _numeros_de_la_tabla(actividades)
    return [c for c in cifras if isinstance(c, Mapping)
            and not _mencionada({float(v) for v in c.get("lecturas") or []}, str(c.get("unidad") or ""), en_tabla)]


def cifras_sin_mencionar(
    adjuntos: Iterable[Mapping[str, Any]], actividades: Sequence[Mapping[str, Any]],
) -> list[CifraDePdf]:
    """Las cifras de alcance de los PDF adjuntos que ninguna actividad de la tabla menciona, las de área primero."""
    leidas: list[CifraDePdf] = []
    vistas: list[tuple[set[float], str]] = []
    for adjunto in adjuntos:
        if not isinstance(adjunto, Mapping):
            continue
        nombre = str(adjunto.get("filename") or "")
        if nombre.lower().endswith(".pdf") and not nombre.startswith("Gmail -"):   # «Gmail - …» es un correo impreso
            leidas.extend(cifras_de_alcance(nombre, str(adjunto.get("text") or ""), vistas))
    pendientes = cifras_aun_sin_mencionar(leidas, actividades)
    ordenadas = sorted(pendientes, key=lambda c: _PRIORIDAD.get(str(c["unidad"]), 9))   # estable: conserva el orden
    return ordenadas[:MAX_CIFRAS]   # type: ignore[return-value]


def pistas_de_cifras(adjuntos: Iterable[Mapping[str, Any]], actividades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """La pista de un hilo: las cifras de sus PDF que la tabla no menciona."""
    return {"cifras_de_pdf_sin_mencionar": cifras_sin_mencionar(adjuntos, actividades)}
