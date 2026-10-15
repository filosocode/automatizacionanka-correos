"""DXF en texto: lo mínimo legible de un plano CAD sin biblioteca externa (U-55)."""

from __future__ import annotations

import re
from collections import Counter

from anka.dominio.attachments import MARCA_FORMATO_ABIERTO, VERSIONES_DWG
from anka.dominio.lectores_texto import Unreadable

_MAX_TEXTOS = 200
_MAX_CAPAS = 200
_FORMATO_MTEXT = re.compile(r"\\[A-Za-z][^;\\]*;|\\P|[{}]")
_ENTIDADES_DE_TEXTO = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}


def _pares(texto: str) -> list[tuple[int, str]]:
    lineas = texto.splitlines()
    pares = []
    for indice in range(0, len(lineas) - 1, 2):
        try:
            pares.append((int(lineas[indice].strip()), lineas[indice + 1].strip()))
        except ValueError:
            break       # deja de leer en la primera línea que no es un código de grupo
    return pares


def _decodificar(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenido.decode("cp1252", errors="replace")


def dxf_text(nombre: str, contenido: bytes) -> str:
    """Versión, capas, conteo de entidades por tipo y textos de un DXF ASCII; la geometría no se interpreta."""
    if contenido.startswith(b"AutoCAD Binary DXF"):
        raise Unreadable(f"DXF binario: no hay lector local, no se vio su contenido; {MARCA_FORMATO_ABIERTO} (PDF)")
    pares = _pares(_decodificar(contenido))
    if not any(codigo == 0 and valor == "SECTION" for codigo, valor in pares):
        raise Unreadable(f"el archivo no parece un DXF de texto; {MARCA_FORMATO_ABIERTO} (PDF)")
    seccion, entidad, version, unidades, anterior = "", "", "", "", ""
    capas: list[str] = []
    tipos: Counter[str] = Counter()
    textos: list[str] = []
    for codigo, valor in pares:
        if codigo == 0:
            entidad = valor
            if seccion == "ENTITIES" and valor not in {"SECTION", "ENDSEC", "EOF"}:
                tipos[valor] += 1
        elif codigo == 2 and entidad == "SECTION":
            seccion = valor
        elif codigo == 9:
            anterior = valor
        elif seccion == "HEADER" and codigo == 1 and anterior == "$ACADVER":
            version = valor
        elif seccion == "HEADER" and codigo == 70 and anterior == "$INSUNITS":
            unidades = valor
        elif seccion == "TABLES" and entidad == "LAYER" and codigo == 2 and valor not in capas:
            capas.append(valor)
        elif seccion == "ENTITIES" and entidad in _ENTIDADES_DE_TEXTO and codigo in {1, 3}:
            limpio = " ".join(_FORMATO_MTEXT.sub(" ", valor).split())
            if limpio and len(textos) < _MAX_TEXTOS:
                textos.append(limpio)
    detalle = f" (AutoCAD {VERSIONES_DWG[version]})" if version in VERSIONES_DWG else ""
    lineas = [f"Archivo DXF: {nombre}; versión: {version or 'no indicada'}{detalle}"
              + (f"; unidades (código {unidades})" if unidades else "")]
    if capas:
        lineas.append(f"Capas ({len(capas)}): " + ", ".join(capas[:_MAX_CAPAS]))
    if tipos:
        lineas.append("Entidades por tipo: " + ", ".join(f"{tipo}={cuenta}" for tipo, cuenta in tipos.most_common()))
    lineas.extend(f"Texto {numero}: {texto}" for numero, texto in enumerate(textos, 1))
    lineas.append("LECTURA PARCIAL: DXF leído solo como texto (capas, conteo de entidades y textos); "
                  "la geometría no se interpreta ni se miden longitudes o áreas")
    return "\n".join(lineas)
