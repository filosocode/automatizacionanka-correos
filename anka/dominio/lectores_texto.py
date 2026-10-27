"""Lectura de texto, HTML y RTF en memoria."""

from __future__ import annotations
from html.parser import HTMLParser
import re
import unicodedata

from anka.dominio.attachments import (
    MAX_CARACTERES_DE_CELDA, MIN_CARACTERES_DE_LINEA_REPETIDA, MIN_CELDAS_SUELTAS_DE_TABLA,
)


class Unreadable(Exception):
    """El adjunto no se puede leer; el motivo se muestra en el dashboard."""


def plain_text(content: bytes) -> str:
    """Decodifica texto y numera sus líneas para conservar localizadores."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    lines = text.splitlines()
    return "\n".join(f"Línea {number}: {line}" for number, line in enumerate(lines, 1))


class _VisibleHtml(HTMLParser):
    """Conserva texto y límites básicos de bloques; descarta etiquetas activas y estilos."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"} and not self.hidden_depth:
            self.parts.append("\n")
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.parts.append(f"\n[Imagen: {alt or 'sin texto alternativo; requiere inspección visual'}]\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"} and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.parts.append(f"\n[Imagen: {alt or 'sin texto alternativo; requiere inspección visual'}]\n")


def _compacto(texto: str) -> str:
    """El texto en minúsculas, sin espacios, signos ni tildes: la forma en que se comparan el OCR y el texto nativo."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))
    return re.sub(r"\W+", "", sin_tildes)


def lineas_nuevas_del_ocr(ocr: str, nativo: str) -> list[str]:
    """Las líneas del OCR que el texto nativo de la página no trae ya (T-124).

    Una línea es repetida si coincide completa con una del texto nativo o si es larga y está contenida en él.
    Una corta no se descarta por estar «contenida»: la celda «150» de una tabla está dentro de «1500 hectáreas».
    """
    lineas_nativas = {_compacto(linea) for linea in nativo.splitlines()}
    nativo_compacto = _compacto(nativo)
    nuevas = []
    for linea in ocr.splitlines():
        compacta = _compacto(linea)
        if not compacta:
            continue
        if compacta in lineas_nativas or (
                len(compacta) >= MIN_CARACTERES_DE_LINEA_REPETIDA and compacta in nativo_compacto):
            continue
        nuevas.append(linea)
    return nuevas


def hay_tabla_en_imagen(lineas: list[str]) -> bool:
    """Si el OCR aportó varias cifras cortas sueltas: la huella de una tabla en imagen (T-124)."""
    sueltas = [linea for linea in lineas
               if len(linea.strip()) <= MAX_CARACTERES_DE_CELDA and re.fullmatch(r"[\d.,]+", linea.strip())]
    return len(sueltas) >= MIN_CELDAS_SUELTAS_DE_TABLA


def reparar_ligaduras_perdidas(texto: str) -> tuple[str, int]:
    """Cambia por «ti» el nulo que deja una ligadura sin mapear; devuelve (texto, ligaduras recuperadas) (T-124).

    Un nulo pegado a una letra («co\\x00zación», «\\x00enen») era siempre «ti» en los casos observados; uno
    aislado no es una letra y se elimina. Es una reparación por heurística: quien la usa debe declararla.
    """
    recuperadas = 0

    def cambiar(coincidencia: re.Match[str]) -> str:
        nonlocal recuperadas
        recuperadas += 1
        return "ti"

    texto = re.sub(r"(?<=[^\W\d_])\x00|\x00(?=[^\W\d_])", cambiar, texto)
    return texto.replace("\x00", ""), recuperadas


def html_text(content: bytes) -> str:
    """Extrae texto visible de HTML e identifica imágenes pendientes de revisión."""
    parser = _VisibleHtml()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    lines = [line.strip() for line in " ".join(parser.parts).splitlines() if line.strip()]
    return plain_text("\n".join(lines).encode("utf-8"))


def rtf_text(content: bytes) -> str:
    """Decodifica controles RTF e informa del contenido gráfico no interpretado."""
    raw = content.decode("latin-1")
    has_images = r"\pict" in raw
    raw = re.sub(
        r"\\'[0-9a-fA-F]{2}",
        lambda m: bytes([int(m.group()[2:], 16)]).decode("cp1252", errors="replace"),
        raw,
    )
    raw = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), raw)
    raw = re.sub(r"\\(par|line|tab)\b", lambda m: "\n" if m.group(1) != "tab" else "\t", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    raw = raw.replace("{", "").replace("}", "")
    if has_images:
        raw += "\nCONTENIDO GRÁFICO RTF NO INSPECCIONADO"
    return plain_text(raw.encode("utf-8"))
