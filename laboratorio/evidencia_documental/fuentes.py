"""Lectura determinista de los originales para comprobar una evidencia (T-117). Solo lee bytes; no escribe nada.

Un `PaqueteDeOriginales` reúne los archivos que ANKA recibió (por nombre), y opcionalmente el texto OCR de cada página
de un PDF escaneado. El OCR es un artefacto aparte: ayuda a localizar texto impreso, pero no certifica tablas, figuras,
tachaduras ni anotaciones (eso lo confirma una persona viendo el original).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePath
from typing import Any

import openpyxl
import pypdf
from openpyxl.utils import column_index_from_string
from openpyxl.utils.cell import coordinate_from_string, range_boundaries
from openpyxl.utils.exceptions import CellCoordinatesException

_ESPACIOS = re.compile(r"\s+")


def sha256(datos: bytes) -> str:
    """SHA-256 en hexadecimal."""
    return hashlib.sha256(datos).hexdigest()


def plano(texto: str) -> str:
    """Texto comparable: sin tildes, en minúsculas y con los espacios colapsados. Las cifras y signos no cambian."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", sin_tildes.casefold()).strip()


def sin_espacios(texto: str) -> str:
    """Para el OCR, que a veces junta palabras («DEREFERENCIA»): se compara sin ningún espacio."""
    return plano(texto).replace(" ", "")


def contiene_cifra(texto: str, cifra: str) -> bool:
    """La cifra aparece completa: «18,5» no se encuentra dentro de «118,5» ni de «18,56»."""
    patron = r"(?<![\d.,])" + re.escape(cifra) + r"(?![\d]|[.,]\d)"
    return re.search(patron, texto) is not None


def unidad_tras_cifra(texto: str, cifra: str, unidad: str, *, pegada: bool = False) -> str:
    """Compara la unidad declarada con la que sigue a la cifra en el texto.

    Devuelve «igual» (la unidad declarada sigue a la cifra), «distinta» (la cifra aparece pero seguida de otra unidad) o
    «ausente» (la cifra no aparece). Con `pegada` (OCR) basta que la unidad siga a la cifra sin espacios; sin ella se
    exige una palabra completa, para que «m» no se acepte dentro de «km».
    """
    cifra_p, unidad_p = plano(cifra), plano(unidad)
    base = plano(texto)
    if pegada:
        base = base.replace(" ", "")
        patron = r"(?<![\d.,])" + re.escape(cifra_p) + r"(?![\d]|[.,]\d)(?P<resto>.{0,24})"
    else:
        patron = r"(?<![\d.,])" + re.escape(cifra_p) + r"(?![\d]|[.,]\d)\s*(?P<resto>.{0,24})"
    encontrada = False
    for m in re.finditer(patron, base):
        encontrada = True
        resto = m.group("resto")
        if pegada:
            if resto.startswith(unidad_p.replace(" ", "")):
                return "igual"
        elif re.match(re.escape(unidad_p) + r"(?![\w²³])", resto):
            return "igual"
    return "distinta" if encontrada else "ausente"


@dataclass
class HojaDeCalculo:
    """Una hoja: su estado de visibilidad, su tamaño usado, fórmulas, valores guardados y encabezados."""

    nombre: str
    estado: str                      # visible, hidden o veryHidden
    filas: int
    columnas: int
    formulas: dict[str, Any] = field(default_factory=dict)
    valores: dict[str, Any] = field(default_factory=dict)
    encabezados: dict[int, str] = field(default_factory=dict)   # columna -> texto de la fila 1
    comentarios: dict[str, str] = field(default_factory=dict)   # coordenada -> texto del comentario de la celda


def literales_de_valor(valor: Any) -> list[str]:
    """Las formas en que un valor guardado se escribiría tal cual: 33.7, 33,7; 24 y 24.0 → «24»."""
    if valor is None:
        return []
    if isinstance(valor, bool):
        return [str(valor)]
    if isinstance(valor, int):
        return [str(valor)]
    if isinstance(valor, float):
        texto = repr(valor)
        formas = [str(int(valor))] if valor.is_integer() else [texto, texto.replace(".", ",")]
        return formas
    return [str(valor)]


class PaqueteDeOriginales:
    """Los originales de un caso, por nombre de archivo, con acceso de solo lectura."""

    def __init__(self, archivos: dict[str, bytes], textos_ocr: dict[str, dict[int, str]] | None = None) -> None:
        self.archivos = {PurePath(k).name: v for k, v in archivos.items()}
        self.textos_ocr = textos_ocr or {}
        self._pdf: dict[str, pypdf.PdfReader] = {}
        self._libros: dict[str, dict[str, HojaDeCalculo]] = {}

    def tiene(self, archivo: str) -> bool:
        """Si el archivo se entregó."""
        return archivo in self.archivos

    def sha256(self, archivo: str) -> str:
        """Huella del archivo entregado."""
        return sha256(self.archivos[archivo])

    # ---- PDF ----
    def _lector_pdf(self, archivo: str) -> pypdf.PdfReader:
        if archivo not in self._pdf:
            self._pdf[archivo] = pypdf.PdfReader(BytesIO(self.archivos[archivo]))
        return self._pdf[archivo]

    def paginas(self, archivo: str) -> int:
        """Número de páginas de un PDF."""
        return len(self._lector_pdf(archivo).pages)

    def texto_nativo(self, archivo: str, pagina: int) -> str:
        """Texto de la capa de texto de la página (vacío si la página es solo imagen). Página desde 1."""
        return self._lector_pdf(archivo).pages[pagina - 1].extract_text() or ""

    def texto_ocr(self, archivo: str, pagina: int) -> str | None:
        """Texto OCR de la página si el paquete lo trae."""
        return self.textos_ocr.get(archivo, {}).get(pagina)

    # ---- Excel ----
    def libro(self, archivo: str) -> dict[str, HojaDeCalculo]:
        """Las hojas de un libro, con fórmulas y valores guardados."""
        if archivo in self._libros:
            return self._libros[archivo]
        datos = self.archivos[archivo]
        con_formulas = openpyxl.load_workbook(BytesIO(datos), data_only=False)
        con_valores = openpyxl.load_workbook(BytesIO(datos), data_only=True)
        hojas: dict[str, HojaDeCalculo] = {}
        for ws in con_formulas.worksheets:
            wv = con_valores[ws.title]
            h = HojaDeCalculo(ws.title, ws.sheet_state, ws.max_row, ws.max_column)
            for fila in ws.iter_rows():
                for c in fila:
                    if c.comment is not None:
                        h.comentarios[c.coordinate] = c.comment.text
                    if c.value is None:
                        continue
                    if isinstance(c.value, str) and c.value.startswith("="):
                        h.formulas[c.coordinate] = c.value
                    h.valores[c.coordinate] = wv[c.coordinate].value
                    if c.row == 1 and isinstance(c.value, str):
                        h.encabezados[c.column] = c.value
            hojas[ws.title] = h
        self._libros[archivo] = hojas
        return hojas

    def celdas_del_rango(self, hoja: HojaDeCalculo, rango: str, *, con_comentarios: bool = False) -> list[str]:
        """Coordenadas ocupadas dentro del rango (con `con_comentarios`, también las que solo tienen comentario).

        Lanza ValueError si el rango no es válido o se sale de la hoja.
        """
        try:
            col_min, fila_min, col_max, fila_max = range_boundaries(rango.replace("$", ""))
        except (ValueError, TypeError, CellCoordinatesException) as exc:
            raise ValueError(f"rango no válido: {rango!r}") from exc
        if col_min is None or fila_min is None or col_max is None or fila_max is None:
            raise ValueError(f"rango abierto no admitido: {rango!r}")
        if fila_min < 1 or col_min < 1 or fila_max > hoja.filas or col_max > hoja.columnas:
            raise ValueError(f"el rango {rango} se sale de la hoja «{hoja.nombre}» (usada hasta {hoja.filas} filas × {hoja.columnas} columnas)")
        ocupadas = []
        for coord in (hoja.valores.keys() | hoja.comentarios.keys() if con_comentarios else hoja.valores):
            letras, fila = coordinate_from_string(coord)
            col = column_index_from_string(letras)
            if col_min <= col <= col_max and fila_min <= fila <= fila_max:
                ocupadas.append(coord)
        return ocupadas
