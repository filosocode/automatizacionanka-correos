"""Agrupar las solicitudes que parecen el mismo proyecto, solo para mostrarlas juntas (U-19).

No une ni cambia nada: es una vista que se calcula al abrir la página. Reutiliza el criterio de las carpetas de Drive
(nombre parecido o mismo archivo, ver `drive_layout`) con tres cuidados para no esconder solicitudes distintas:
el mismo remitente no basta, dos clientes distintos no se agrupan por el nombre y dos ofertas con T distinta
nunca son el mismo proyecto.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from anka.servicios.drive_layout import clave_de_proyecto, claves_parecidas, normalize_client_name

MOTIVO_ARCHIVO = "mismo archivo"
MOTIVO_CLIENTE = "mismo cliente y nombre parecido"
MOTIVO_NOMBRE = "nombre parecido"
_FUERZA = {MOTIVO_ARCHIVO: 3, MOTIVO_CLIENTE: 2, MOTIVO_NOMBRE: 1}
# Solo documentos: las firmas de correo traen siempre las mismas imágenes y no dicen nada del proyecto.
_DOCUMENTOS = frozenset({
    "pdf", "doc", "docx", "xls", "xlsx", "xlsm", "csv", "kml", "kmz", "dwg", "dxf", "zip", "rar", "shp",
    "gpx", "ppt", "pptx", "odt", "ods", "txt", "rtf"})
_SIN_CLIENTE = frozenset({"", "no indicado", "no clasificado", "sin cliente"})


@dataclass(frozen=True, eq=False)
class Grupo:
    """Hilos que parecen el mismo proyecto, en el orden de la lista, y por qué se agruparon."""

    miembros: tuple[Mapping[str, Any], ...]
    motivo: str = ""

    @property
    def verificar(self) -> bool:
        """Solo el nombre se parece: conviene que una persona confirme que es el mismo proyecto."""
        return self.motivo == MOTIVO_NOMBRE


@dataclass(frozen=True)
class _Ficha:
    clave: str
    palabras: frozenset[str]
    numeros: frozenset[str]
    archivos: frozenset[tuple[str, int]]      # (nombre, caracteres leídos; 0 si no se pudo leer)
    correo: str
    cliente: str
    codigo: str


def _ficha(quote: Mapping[str, Any]) -> _Ficha:
    analisis = quote.get("analysis") or {}
    nombre = str(analisis.get("project_name") or "")
    clave = clave_de_proyecto(nombre) if nombre.strip() else ""
    palabras = frozenset(clave.split())
    archivos = set()
    for adjunto in analisis.get("attachments") or []:
        if not isinstance(adjunto, Mapping):
            continue
        archivo = str(adjunto.get("filename") or "").strip().casefold()
        if "." in archivo and archivo.rsplit(".", 1)[-1] in _DOCUMENTOS:
            archivos.add((archivo, int(adjunto.get("extracted_chars") or 0)))
    # «OTACC (razón social no indicada)» es el cliente «OTACC»: lo que va entre paréntesis es una nota.
    nombre_cliente = str(quote.get("client_name") or "")
    # Una dirección de correo no es el nombre de una empresa: sin empresa, el cliente no está identificado.
    cliente = "" if "@" in nombre_cliente else normalize_client_name(re.sub(r"\([^)]*\)", " ", nombre_cliente))
    return _Ficha(
        clave=clave, palabras=palabras, numeros=frozenset(p for p in palabras if re.search(r"\d", p)),
        archivos=frozenset(archivos), correo=str(quote.get("client_email") or "").strip().casefold(),
        cliente="" if cliente in _SIN_CLIENTE else cliente, codigo=str(quote.get("quote_code") or ""))


def _comparten_archivo(a: _Ficha, b: _Ficha) -> bool:
    """El mismo documento: igual nombre e igual cantidad de texto leído; un nombre genérico solo no basta."""
    return any(na == nb and ca > 0 and ca == cb for na, ca in a.archivos for nb, cb in b.archivos)


def _clientes_distintos(a: _Ficha, b: _Ficha) -> bool:
    return bool(a.cliente and b.cliente and not _mismo_cliente(a, b))


def _mismo_cliente(a: _Ficha, b: _Ficha) -> bool:
    if a.correo and a.correo == b.correo:
        return True
    if not a.cliente or not b.cliente:
        return False
    return claves_parecidas(a.cliente, b.cliente)


def _motivo(a: _Ficha, b: _Ficha) -> str:
    """Por qué dos hilos parecen el mismo proyecto, o cadena vacía si no lo parecen."""
    if a.codigo and b.codigo and a.codigo != b.codigo:
        return ""                                   # dos ofertas distintas no son el mismo proyecto
    if _clientes_distintos(a, b):
        return ""                                   # dos clientes distintos: ni por el nombre ni por un archivo
    if _comparten_archivo(a, b):
        return MOTIVO_ARCHIVO
    if not a.clave or not b.clave or not (a.palabras & b.palabras):
        return ""
    if a.numeros != b.numeros or not claves_parecidas(a.clave, b.clave):
        return ""                                   # «Calle 170» y «Calle 171» se parecen en letras, no en proyecto
    if _mismo_cliente(a, b):
        return MOTIVO_CLIENTE
    return MOTIVO_NOMBRE


def motivo_entre(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """Por qué dos hilos concretos parecen el mismo proyecto, o cadena vacía si entre ellos no hay parecido directo."""
    return _motivo(_ficha(a), _ficha(b))


def agrupar(quotes: Sequence[Mapping[str, Any]], separados: Collection[frozenset[str]] = ()) -> list[Grupo]:
    """Todos los hilos, agrupados por proyecto; los que están solos van en un grupo de uno.

    Los grupos salen en el orden en que aparece su primer hilo y los hilos de cada uno en el orden de la lista.
    `separados` son pares de hilos que una persona dijo que no son el mismo proyecto: entre ellos no hay parecido.
    """
    fichas = [_ficha(q) for q in quotes]
    padre = list(range(len(quotes)))

    def raiz(i: int) -> int:
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    aristas: list[tuple[int, int, str]] = []
    for i in range(len(quotes)):
        for j in range(i + 1, len(quotes)):
            if frozenset((str(quotes[i].get("thread_id")), str(quotes[j].get("thread_id")))) in separados:
                continue
            motivo = _motivo(fichas[i], fichas[j])
            if motivo:
                aristas.append((i, j, motivo))
                padre[raiz(j)] = raiz(i)
    motivos: dict[int, str] = {}
    for i, _, motivo in aristas:
        r = raiz(i)
        if _FUERZA[motivo] > _FUERZA.get(motivos.get(r, ""), 0):
            motivos[r] = motivo
    miembros: dict[int, list[Mapping[str, Any]]] = {}
    for i, quote in enumerate(quotes):
        miembros.setdefault(raiz(i), []).append(quote)
    return [Grupo(tuple(hilos), motivos.get(r, "")) for r, hilos in miembros.items()]
