"""Shapefile y tabla dBase leídos con la biblioteca estándar, aunque falten sus archivos hermanos (U-55)."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping

from anka.dominio.attachments import MAX_EVIDENCE_ITEMS
from anka.dominio.lectores_texto import Unreadable

TIPOS_DE_GEOMETRIA = {
    0: "Null", 1: "Point", 3: "PolyLine", 5: "Polygon", 8: "MultiPoint", 11: "PointZ", 13: "PolyLineZ",
    15: "PolygonZ", 18: "MultiPointZ", 21: "PointM", 23: "PolyLineM", 25: "PolygonM", 28: "MultiPointM",
    31: "MultiPatch",
}
_CODIGO_SHP = 9994
_MAX_CAMPOS = 255


def cabecera_shp(shp: bytes) -> tuple[str, tuple[float, float, float, float]]:
    """Tipo de geometría y extensión XY (mín X, mín Y, máx X, máx Y) de la cabecera de 100 bytes de un `.shp`."""
    if len(shp) < 100 or struct.unpack(">i", shp[:4])[0] != _CODIGO_SHP:
        raise Unreadable("el archivo .shp no tiene la cabecera de un shapefile (¿está dañado o es otro formato?)")
    codigo = struct.unpack("<i", shp[32:36])[0]
    caja = struct.unpack("<4d", shp[36:68])
    return TIPOS_DE_GEOMETRIA.get(codigo, f"tipo {codigo}"), (caja[0], caja[1], caja[2], caja[3])


def contar_registros_shp(shp: bytes) -> tuple[int, bool]:
    """(registros, ¿el archivo termina limpio?) recorriendo las cabeceras de registro de 8 bytes."""
    posicion, cuenta = 100, 0
    while posicion + 8 <= len(shp):
        palabras = struct.unpack(">i", shp[posicion + 4:posicion + 8])[0]
        if palabras < 0:
            return cuenta, False
        posicion += 8 + palabras * 2
        if posicion > len(shp):
            return cuenta, False
        cuenta += 1
    return cuenta, posicion == len(shp)


def _decodificar(valor: bytes, codificacion: str) -> str:
    try:
        return valor.decode(codificacion).strip("\x00 ")
    except UnicodeDecodeError:
        return valor.decode("cp1252", errors="replace").strip("\x00 ")


def _codificacion(cpg: bytes | None) -> str:
    if not cpg:
        return "utf-8"
    nombre = cpg.decode("ascii", errors="ignore").strip().casefold()
    return "cp1252" if nombre in {"1252", "cp1252", "windows-1252"} else "utf-8"


def leer_dbf(dbf: bytes, cpg: bytes | None = None) -> tuple[int, list[tuple[str, int]], list[list[str]]]:
    """(registros declarados, [(campo, ancho)], primeros registros) de una tabla dBase III/IV."""
    if len(dbf) < 33:
        raise Unreadable("el archivo .dbf es demasiado corto para ser una tabla dBase")
    declarados = struct.unpack("<I", dbf[4:8])[0]
    cabecera, largo = struct.unpack("<HH", dbf[8:12])
    codificacion = _codificacion(cpg)
    campos: list[tuple[str, int]] = []
    posicion = 32
    while posicion + 32 <= len(dbf) and dbf[posicion] != 0x0D and len(campos) < _MAX_CAMPOS:
        campos.append((_decodificar(dbf[posicion:posicion + 11].split(b"\x00")[0], codificacion), dbf[posicion + 16]))
        posicion += 32
    registros: list[list[str]] = []
    if largo and campos and cabecera:
        for indice in range(min(declarados, MAX_EVIDENCE_ITEMS)):
            inicio = cabecera + indice * largo
            if inicio + largo > len(dbf):
                break
            if dbf[inicio:inicio + 1] == b"*":      # registro borrado
                continue
            cursor, fila = inicio + 1, []
            for _, ancho in campos:
                fila.append(_decodificar(dbf[cursor:cursor + ancho], codificacion))
                cursor += ancho
            registros.append(fila)
    return declarados, campos, registros


def _registros_en_texto(campos: list[tuple[str, int]], registros: list[list[str]], declarados: int) -> list[str]:
    lineas = [
        f"Registro {numero}: " + "; ".join(f"{campo}={valor}" for (campo, _), valor in zip(campos, fila, strict=False))
        for numero, fila in enumerate(registros, 1)
    ]
    if declarados > MAX_EVIDENCE_ITEMS:
        lineas.append(f"LECTURA PARCIAL: se muestran {MAX_EVIDENCE_ITEMS} registros de atributos")
    return lineas


def shapefile_text(capa: str, shp: bytes, companeros: Mapping[str, bytes] | None = None) -> str:
    """Describe una capa: geometría, extensión, registros y campos; marca como parcial lo que falte.

    `companeros` asigna a cada extensión en minúsculas (`.shx`, `.dbf`, `.prj`, `.cpg`) el contenido del archivo
    hermano, si se tiene. Con solo el `.shp` se leen la cabecera y el número de registros.
    """
    companeros = companeros or {}
    tipo, caja = cabecera_shp(shp)
    cuenta, limpio = contar_registros_shp(shp)
    dbf = companeros.get(".dbf")
    declarados, campos, registros = leer_dbf(dbf, companeros.get(".cpg")) if dbf else (0, [], [])
    encabezado = f"Capa shapefile: {capa}; geometría: {tipo}; elementos: {cuenta}"
    if campos:
        encabezado += "; campos: " + ", ".join(campo for campo, _ in campos)
    lineas = [encabezado]
    if all(math.isfinite(valor) for valor in caja) and cuenta:
        lineas.append("Extensión XY (mín X, mín Y, máx X, máx Y): " + ", ".join(map(str, caja)))
    if not limpio:
        lineas.append("LECTURA PARCIAL: el .shp termina incompleto; el número de elementos puede ser menor al real")
    if not dbf:
        lineas.append("LECTURA PARCIAL: falta el .dbf; no se leyeron los atributos (campos y valores) de la capa")
    elif declarados != cuenta:
        lineas.append(f"LECTURA PARCIAL: el .dbf declara {declarados} registros y el .shp tiene {cuenta}")
    if ".shx" not in companeros:
        lineas.append("LECTURA PARCIAL: falta el .shx (índice); el número de elementos se contó recorriendo el .shp")
    lineas.extend(_registros_en_texto(campos, registros, declarados))
    proyeccion = companeros.get(".prj")
    if proyeccion:
        lineas.append("Sistema de coordenadas (PRJ): " + proyeccion.decode("utf-8", errors="replace"))
    else:
        lineas.append("SISTEMA DE COORDENADAS NO DECLARADO: el shapefile no incluye archivo .prj")
    return "\n".join(lineas)


def dbf_text(nombre: str, dbf: bytes, cpg: bytes | None = None) -> str:
    """Una tabla `.dbf` recibida sola: registros, campos y primeros valores."""
    declarados, campos, registros = leer_dbf(dbf, cpg)
    lineas = [f"Tabla dBase {nombre}; registros: {declarados}; campos: " + ", ".join(c for c, _ in campos),
              "LECTURA PARCIAL: tabla de atributos recibida sin su .shp; falta la geometría"]
    lineas.extend(_registros_en_texto(campos, registros, declarados))
    return "\n".join(lineas)
