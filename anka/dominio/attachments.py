"""Extraccion local de texto desde adjuntos (sin llamar al modelo)."""

from __future__ import annotations

import html
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024   # un adjunto mayor no se lee: protege la memoria y el tiempo del análisis
MAX_ATTACHMENT_TEXT = 30000   # texto que se entrega al modelo por adjunto; más no cabe útilmente en su contexto
TEXT_ATTACHMENT_EXTENSIONS = {
    ".csv", ".geojson", ".json", ".kml", ".md", ".tab", ".tsv", ".txt", ".xml",
}


def _kmz_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        kml_files = [name for name in archive.namelist() if name.casefold().endswith(".kml")]
        if not kml_files:
            raise ValueError("el KMZ no contiene un archivo KML")
        summaries = []
        for name in kml_files:
            raw = archive.read(name).decode("utf-8", errors="replace")
            feature_names = [
                html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
                for value in re.findall(r"<name>(.*?)</name>", raw, re.IGNORECASE | re.DOTALL)
            ]
            coordinates = []
            for block in re.findall(
                r"<coordinates>(.*?)</coordinates>", raw, re.IGNORECASE | re.DOTALL
            ):
                for value in block.split():
                    parts = value.split(",")
                    if len(parts) < 2:
                        continue
                    try:
                        coordinates.append((float(parts[0]), float(parts[1])))
                    except ValueError:
                        continue
            lines = [f"Archivo KML: {name}"]
            if feature_names:
                lines.append("Elementos: " + "; ".join(dict.fromkeys(feature_names))[:3000])
            lines.append(f"Cantidad de coordenadas: {len(coordinates)}")
            if coordinates:
                west = min(point[0] for point in coordinates)
                south = min(point[1] for point in coordinates)
                east = max(point[0] for point in coordinates)
                north = max(point[1] for point in coordinates)
                lines.append(
                    "Extensión geográfica (oeste, sur, este, norte): "
                    f"{west}, {south}, {east}, {north}"
                )
            summaries.append("\n".join(lines))
        return "\n\n".join(summaries)


class _Unreadable(Exception):
    """El adjunto no se puede leer; el mensaje es el motivo que se le muestra a la persona."""


def _plain_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise _Unreadable("falta instalar pypdf para leer PDF") from None
    page_texts = [page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages]
    if not any(page.strip() for page in page_texts):
        raise _Unreadable("PDF sin texto extraíble; puede ser un documento escaneado")
    return "\n".join(
        f"[Página {number}]\n{page}" for number, page in enumerate(page_texts, 1) if page.strip()
    )


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as document:
        root = ElementTree.fromstring(document.read("word/document.xml"))
    return "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))


def _xlsx_text(content: bytes) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise _Unreadable("falta instalar openpyxl para leer Excel") from None
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    rows = []
    for sheet in workbook.worksheets:
        rows.append(f"Hoja: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            if any(value is not None for value in row):
                rows.append("\t".join("" if value is None else str(value) for value in row))
    return "\n".join(rows)


def _read_by_format(extension: str, mime_type: str, content: bytes) -> str:
    """Texto del adjunto según su formato; lanza `_Unreadable` si no se puede o no se debe leer."""
    if extension in TEXT_ATTACHMENT_EXTENSIONS or mime_type.startswith("text/"):
        return _plain_text(content)
    if extension == ".kmz" or mime_type == "application/vnd.google-earth.kmz":
        return _kmz_text(content)
    if extension == ".pdf" or mime_type == "application/pdf":
        return _pdf_text(content)
    if extension == ".docx":
        return _docx_text(content)
    if extension in {".xlsx", ".xlsm"}:
        return _xlsx_text(content)
    if extension in {".dwg", ".dxf"}:
        raise _Unreadable("plano CAD requiere revisión manual")
    raise _Unreadable(f"formato no soportado ({extension or mime_type or 'desconocido'})")


def extract_attachment_text(filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
    """Devuelve texto y motivo de bloqueo; un motivo vacío significa extracción exitosa."""
    if len(content) > MAX_ATTACHMENT_BYTES:
        return "", f"supera el límite de {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"
    extension = Path(filename).suffix.casefold()
    try:
        text = _read_by_format(extension, mime_type, content)
    except _Unreadable as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"no se pudo leer: {exc}"
    text = text.strip()
    if not text:
        return "", "el archivo no contiene texto extraíble"
    return text[:MAX_ATTACHMENT_TEXT], ""
