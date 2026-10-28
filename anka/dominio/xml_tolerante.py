"""Lectura de XML de adjuntos (KML, GPX, WPML) tolerante a prefijos sin declarar y sin entidades (U-55)."""

from __future__ import annotations

import re
from xml.etree import ElementTree

from anka.dominio.lectores_texto import Unreadable

# Prefijos que los programas de mapas escriben a veces sin declararlos; con su espacio de nombres real, para que
# `local_name` y los lectores los traten igual que si estuvieran declarados.
PREFIJOS_CONOCIDOS = {
    "gx": "http://www.google.com/kml/ext/2.2",
    "atom": "http://www.w3.org/2005/Atom",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xal": "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0",
    "kml": "http://www.opengis.net/kml/2.2",
}
_USO_DE_PREFIJO = re.compile(rb"</?([A-Za-z_][\w.\-]*):[A-Za-z_]|[\s\"']([A-Za-z_][\w.\-]*):[A-Za-z_][\w.\-]*\s*=")
_DECLARADO = re.compile(rb"xmlns:([\w.\-]+)\s*=")
_RAIZ = re.compile(rb"<!--.*?-->|<\?.*?\?>|<!DOCTYPE[^>]*>|<(?P<raiz>[A-Za-z_][^\s/>]*)", re.DOTALL)


def _sin_entidades(contenido: bytes) -> None:
    """Rechaza un XML que declara entidades: ni externas (XXE) ni internas (expansión de entidades)."""
    if b"<!ENTITY" in contenido or b"<!ENTITY" in contenido.replace(b"\x00", b""):
        raise Unreadable("el XML declara entidades (<!ENTITY); no se leen por seguridad")


def _prefijos_sin_declarar(contenido: bytes) -> list[str]:
    usados = {a or b for a, b in _USO_DE_PREFIJO.findall(contenido)}
    declarados = set(_DECLARADO.findall(contenido)) | {b"xml", b"xmlns"}
    return sorted(prefijo.decode("ascii") for prefijo in usados - declarados)


def _declarar_en_la_raiz(contenido: bytes, prefijos: list[str]) -> bytes:
    for coincidencia in _RAIZ.finditer(contenido):
        if coincidencia.group("raiz"):
            fin = coincidencia.end()
            declaraciones = "".join(
                f' xmlns:{p}="{PREFIJOS_CONOCIDOS.get(p, "urn:anka:prefijo-no-declarado:" + p)}"' for p in prefijos
            ).encode("ascii")
            return contenido[:fin] + declaraciones + contenido[fin:]
    return contenido


def parsear_xml(contenido: bytes) -> tuple[ElementTree.Element, list[str]]:
    """Analiza el XML y devuelve (raíz, prefijos que se declararon por el archivo).

    Con un prefijo sin declarar (`gx:`, `atom:`) el análisis normal falla con «unbound prefix»; entonces se declaran
    en la raíz los prefijos usados y se reintenta. No se pierde ningún elemento ni atributo. Un XML con `<!ENTITY>` se
    rechaza (el analizador estándar tampoco resuelve entidades externas, así que no hay XXE ni expansión).
    """
    _sin_entidades(contenido)
    try:
        return ElementTree.fromstring(contenido), []
    except ElementTree.ParseError as exc:
        if "unbound prefix" not in str(exc):
            raise
    prefijos = _prefijos_sin_declarar(contenido)
    if not prefijos:
        raise ElementTree.ParseError("unbound prefix")
    return ElementTree.fromstring(_declarar_en_la_raiz(contenido, prefijos)), prefijos
