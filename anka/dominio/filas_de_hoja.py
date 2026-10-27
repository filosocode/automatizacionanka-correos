"""Las filas con cantidad de una hoja de cálculo o una tabla de Word que la tabla no recoge (T-127, T-129).

Lee el texto que el lector dejó (`Hoja: …` con `B6=…| C6=240 | …`, o `Tabla 1, fila 4: columna 1=… | columna 4=50`)
y lo contrasta con las actividades. Solo señala: no agrega filas a la tabla ni decide si una fila es del cliente o un
cálculo interno.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from anka.dominio.analisis import Actividad, FilaDeHoja

MAX_FILAS_GUARDADAS = 100
MAX_LARGO_DE_DESCRIPCION = 600   # la celda de una tabla de Word puede traer párrafos enteros de condiciones
MAX_FILAS_POR_ARCHIVO = 60   # una hoja con cientos de filas es una base de datos, no una plantilla de cantidades
_CELDA = re.compile(r"(?:^| \| )(?P<col>[A-Z]{1,3})(?P<fila>\d+)=", re.MULTILINE)
_HOJA = re.compile(r"^Hoja: (?P<nombre>.*?) ?; visibilidad: (?P<visibilidad>[^;]*);", re.MULTILINE)
_FILA_DE_TABLA = re.compile(r"^Tabla (?P<tabla>\d+), fila (?P<fila>\d+): ", re.MULTILINE)
_CELDA_DE_TABLA = re.compile(r"(?:^|\s\|\s)columna (?P<col>\d+)=")
_VALOR_GUARDADO = re.compile(r"\[valor guardado: ([^\]]+)\]")
_NO_ES_ACTIVIDAD = re.compile(
    r"^(sub)?total|^iva\b|^aiu\b|^valor\b|^administraci|^imprevisto|^utilidad|^descuento|^resumen|^costos? directos?$")
_PALABRAS_SIN_PESO = frozenset(
    "para como desde hasta entre sobre incluye incluyendo segun cada esta este estos donde debe todo todos con del "
    "las los una uno unos unas que por".split())
_LARGO_DE_RAIZ = 6   # «sondeo» y «sondeos», «topografía» y «topográfico» comparten raíz


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))


def _numero(celda: str) -> float | None:
    """El valor numérico de una celda: el número, o el valor guardado de una fórmula; `None` si no lo es."""
    if celda.startswith("="):
        guardado = _VALOR_GUARDADO.search(celda)
        celda = guardado.group(1).strip() if guardado else ""
    if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", celda):
        return None
    return float(celda.replace(",", "."))


def _indice_de_columna(letras: str) -> int:
    indice = 0
    for letra in letras:
        indice = indice * 26 + ord(letra) - 64
    return indice


def _filas_de_la_hoja(bloque: str) -> list[tuple[int, dict[str, str]]]:
    """Las celdas de cada fila de una hoja, en el orden del archivo: (número de fila, {columna: texto})."""
    marcas = list(_CELDA.finditer(bloque))
    por_fila: dict[int, dict[str, str]] = {}
    for n, marca in enumerate(marcas):
        fin = marcas[n + 1].start() if n + 1 < len(marcas) else len(bloque)
        por_fila.setdefault(int(marca["fila"]), {})[marca["col"]] = bloque[marca.end():fin].strip()
    return sorted(por_fila.items())


def _cabecera(celdas: Mapping[str, str]) -> dict[str, str] | None:
    """Las columnas de una fila de encabezado de cantidades: `cantidad` y, si las hay, `unidad` y `veces`."""
    columnas: dict[str, str] = {}
    for col, texto in celdas.items():
        limpio = _sin_tildes(texto).strip()
        for clave in ("cantidad", "unidad", "veces"):
            if re.match(rf"^{clave}\b", limpio) and clave not in columnas:
                columnas[clave] = col
    return columnas if "cantidad" in columnas else None


def _descripcion(celdas: Mapping[str, str], cabecera: Mapping[str, str]) -> str:
    """El texto más largo a la izquierda de la cantidad (descarta la columna de unidad)."""
    limite = _indice_de_columna(cabecera["cantidad"])
    candidatos = [t for c, t in celdas.items()
                  if _indice_de_columna(c) < limite and c != cabecera.get("unidad") and _numero(t) is None
                  and not t.startswith("=") and re.search(r"[^\W\d_]{3}", t)]
    return " ".join(max(candidatos, key=len).split()) if candidatos else ""


def filas_de_hoja(archivo: str, texto: str) -> list[FilaDeHoja]:
    """Las filas con descripción y cantidad positiva de las hojas visibles de un archivo leído."""
    resultado: list[FilaDeHoja] = []
    partes = _HOJA.split(texto)   # [antes, nombre, visibilidad, cuerpo, nombre, visibilidad, cuerpo, …]
    for nombre, visibilidad, cuerpo in zip(partes[1::3], partes[2::3], partes[3::3], strict=False):
        if visibilidad.strip() != "visible":
            continue
        cabecera: dict[str, str] | None = None
        for numero_de_fila, celdas in _filas_de_la_hoja(cuerpo):
            encabezado = _cabecera(celdas)
            if encabezado:
                cabecera = {**(cabecera or {}), **encabezado} if cabecera else encabezado
                continue
            if not cabecera or cabecera["cantidad"] not in celdas:
                continue
            cantidad = _numero(celdas[cabecera["cantidad"]])
            descripcion = _descripcion(celdas, cabecera)
            if not cantidad or cantidad <= 0 or not descripcion or _NO_ES_ACTIVIDAD.match(_sin_tildes(descripcion)):
                continue
            veces = _numero(celdas.get(cabecera.get("veces", ""), "")) or 1.0
            resultado.append({"archivo": archivo, "hoja": nombre, "fila": numero_de_fila, "descripcion": descripcion,
                              "cantidad": cantidad, "veces": veces,
                              "unidad": celdas.get(cabecera.get("unidad", ""), "").strip()})
    return resultado[:MAX_FILAS_POR_ARCHIVO]


def _celdas_de_la_fila_de_tabla(texto: str) -> dict[int, str]:
    """Las celdas de una fila de tabla de Word (`columna 2=…`); lo que sigue en otra línea es parte de su celda."""
    marcas = list(_CELDA_DE_TABLA.finditer(texto))
    return {int(m["col"]): texto[m.end():(marcas[n + 1].start() if n + 1 < len(marcas) else len(texto))].strip()
            for n, m in enumerate(marcas)}


def filas_de_tablas_de_word(archivo: str, texto: str) -> list[FilaDeHoja]:
    """Las filas con descripción y cantidad positiva de las tablas de un Word, bajo un encabezado «Cantidad»."""
    filas = list(_FILA_DE_TABLA.finditer(texto))
    resultado: list[FilaDeHoja] = []
    cabeceras: dict[str, dict[str, int]] = {}
    for n, fila in enumerate(filas):
        fin = filas[n + 1].start() if n + 1 < len(filas) else len(texto)
        celdas = _celdas_de_la_fila_de_tabla(texto[fila.end():fin])
        encabezado: dict[str, int] = {}
        for col, celda in celdas.items():
            for clave in ("cantidad", "unidad", "veces"):
                if re.match(rf"^{clave}\b", _sin_tildes(celda).strip()) and clave not in encabezado:
                    encabezado[clave] = col
        if "cantidad" in encabezado:
            cabeceras[fila["tabla"]] = encabezado
            continue
        cabecera = cabeceras.get(fila["tabla"])
        if not cabecera or "cantidad" not in cabecera:
            continue
        cantidad = _numero(celdas.get(cabecera["cantidad"], ""))
        candidatos = [t for c, t in celdas.items() if c < cabecera["cantidad"] and c != cabecera.get("unidad")
                      and _numero(t) is None and re.search(r"[^\W\d_]{3}", t)]
        descripcion = " ".join(max(candidatos, key=len).split()) if candidatos else ""
        if not cantidad or cantidad <= 0 or not descripcion or _NO_ES_ACTIVIDAD.match(_sin_tildes(descripcion)):
            continue
        if len(descripcion) > MAX_LARGO_DE_DESCRIPCION:
            descripcion = descripcion[:MAX_LARGO_DE_DESCRIPCION].rstrip() + "…"
        resultado.append({"archivo": archivo, "hoja": f"Tabla {fila['tabla']}", "fila": int(fila["fila"]),
                          "descripcion": descripcion, "cantidad": cantidad,
                          "veces": _numero(celdas.get(cabecera.get("veces", 0), "")) or 1.0,
                          "unidad": celdas.get(cabecera.get("unidad", 0), "").strip()})
    return resultado[:MAX_FILAS_POR_ARCHIVO]


def _raices(texto: str) -> set[str]:
    palabras = re.findall(r"[a-z0-9]{4,}", _sin_tildes(texto))
    return {p[:_LARGO_DE_RAIZ] for p in palabras if p not in _PALABRAS_SIN_PESO and not p.isdigit()}


def _recoge(fila: Mapping[str, Any], actividades: Sequence[Mapping[str, Any]]) -> bool:
    """Si alguna actividad habla de lo mismo que la fila: comparten raíces, o una raíz y la misma cantidad."""
    de_la_fila = _raices(fila["descripcion"])
    for actividad in actividades:
        de_la_actividad = " ".join(str(actividad.get(k) or "") for k in ("activity", "entregable", "notes"))
        compartidas = de_la_fila & _raices(de_la_actividad)
        misma_cantidad = _numero(str(actividad.get("quantity") or "").strip()) == fila["cantidad"]
        if len(compartidas) >= 2 or (compartidas and (len(de_la_fila) <= 2 or misma_cantidad)):
            return True
    return False


def filas_sin_cubrir(
    adjuntos: Iterable[Mapping[str, Any]], actividades: Sequence[Mapping[str, Any]],
) -> list[FilaDeHoja]:
    """Las filas con cantidad de las hojas de cálculo adjuntas que ninguna actividad de la tabla recoge."""
    actividades = [a for a in actividades if isinstance(a, Mapping)]
    sin_cubrir: list[FilaDeHoja] = []
    for adjunto in adjuntos:
        texto = str(adjunto.get("text") or "") if isinstance(adjunto, Mapping) else ""
        nombre = str(adjunto.get("filename") or "") if isinstance(adjunto, Mapping) else ""
        if nombre.lower().endswith((".xlsx", ".xlsm")) and "Hoja: " in texto:
            leidas = filas_de_hoja(nombre, texto)
        elif nombre.lower().endswith(".docx") and "Tabla " in texto:
            leidas = filas_de_tablas_de_word(nombre, texto)
        else:
            continue
        sin_cubrir.extend(f for f in leidas if not _recoge(f, actividades))
    return sin_cubrir[:MAX_FILAS_GUARDADAS]


def filas_aun_sin_cubrir(
    filas: Iterable[Mapping[str, Any]], actividades: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Las filas guardadas como pista que la tabla de ahora todavía no recoge (la persona pudo agregar algunas)."""
    actuales = [a for a in actividades if isinstance(a, Mapping)]
    return [f for f in filas if isinstance(f, Mapping) and not _recoge(f, actuales)]


def clave_de_fila(fila: Mapping[str, Any]) -> str:
    """Identifica una fila de hoja de cálculo en un formulario: archivo, hoja y número de fila."""
    return f"{fila.get('archivo')}|{fila.get('hoja')}|{fila.get('fila')}"


def actividad_de_fila(fila: Mapping[str, Any]) -> Actividad:
    """La actividad que una persona agrega desde una fila de hoja: lo que dice la hoja, `pendiente` y con su fuente."""
    cantidad, veces = float(fila.get("cantidad") or 0), float(fila.get("veces") or 1)
    nota = "Agregada por una persona desde un archivo adjunto; completa ubicación, entregable y plazo."
    if veces != 1:
        nota += f" El archivo dice {cantidad:g} × {veces:g} veces."
    return {"activity": str(fila.get("descripcion") or ""), "quantity": f"{cantidad:g}".replace(".", ","),
            "unit": str(fila.get("unidad") or ""),
            "fuente": f"{fila.get('archivo')} · hoja «{fila.get('hoja')}» · fila {fila.get('fila')}",
            "estado": "pendiente", "notes": nota}


def pistas_de_hoja(adjuntos: Iterable[Mapping[str, Any]], actividades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """La pista de un hilo: las filas de sus hojas de cálculo que la tabla no recoge."""
    return {"filas_de_hoja_sin_cubrir": filas_sin_cubrir(adjuntos, actividades)}
