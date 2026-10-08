"""Límites y evidencia de adjuntos, sin acceso al disco ni servicios externos."""

from __future__ import annotations
from anka.dominio.analisis import EvidenciaAdjunto
import re

# Gmail admite adjuntos de hasta 25 MB; 50 MiB deja cabida a cualquiera y es el mismo tope en los tres modos.
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_WORK_PACKET_ATTACHMENT_BYTES = MAX_ATTACHMENT_BYTES
MAX_EVIDENCE_ITEMS = 60
MAX_EVIDENCE_CHARS = 700
MAX_ARCHIVE_FILES = 200
MAX_ARCHIVE_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_SPREADSHEET_CELLS = 50000
MAX_OCR_PAGES = 200
MAX_OCR_IMAGES = 100
MAX_OCR_PIXELS = 40_000_000
TEXT_ATTACHMENT_EXTENSIONS = {
    ".csv",
    ".geojson",
    ".gpx",
    ".json",
    ".kml",
    ".md",
    ".plan",
    ".tab",
    ".tsv",
    ".txt",
    ".waypoints",
    ".wpml",
    ".xml",
}
KML_GEOMETRIES = {"Point", "LineString", "Polygon", "Track", "Model"}


def _evidence_location(line: str, current: str) -> tuple[str, str]:
    match = re.match(r"^\[(Página\s+\d+)(?:;([^\]]+))?\]\s*(.*)$", line)
    if match:
        detail = " ".join(part for part in (match.group(2), match.group(3)) if part)
        return match.group(1), detail.strip()
    if line.startswith("Hoja: "):
        return line.split(";", 1)[0], ""
    if line.startswith("Archivo KML:"):
        return line, ""
    if line.startswith("Archivo dentro de ZIP:"):
        return line, ""
    if line.startswith("LECTURA PARCIAL DEL ZIP"):
        return "Cobertura del ZIP", line
    if line.startswith("Elementos geográficos:"):
        return "Resumen KML", line
    if line.startswith(("Geometría:", "Muestras (longitud, latitud):", "Extensión geográfica KML")):
        return current, line
    if line.startswith("[OCR visual adicional]"):
        return current, line
    if line.startswith("[") and line.endswith("]"):
        return line.strip("[]"), ""
    if line.startswith("Párrafo ") or line.startswith("Tabla ") or line.startswith("Elemento: "):
        return line.split(":", 1)[0], line.partition(":")[2].strip()
    structured = re.match(
        r"^(Waypoint(?:\s+\d+)?|Ruta\s+[^:]+|Track\s+[^:]+|Segmento\s+\d+|"
        r"Punto ruta\s+\d+|Elemento\s+\d+|Feature\s+\d+|Capa shapefile|Registro\s+\d+):?\s*(.*)$",
        line,
    )
    if structured:
        return structured.group(1), structured.group(2)
    if re.match(r"^[A-Z]{1,3}\d+=", line):
        return current, line
    return current, line


def attachment_reviewed_sections(text: str) -> list[str]:
    """Enumera todos los localizadores del texto extraído, sin el límite de citas de la ficha."""
    sections = []
    current = "Contenido extraído"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        location, _ = _evidence_location(line, current)
        if location != current:
            current = location
            if current not in sections:
                sections.append(current)
    return sections or ["Contenido sin paginación"]


def build_attachment_evidence(text: str) -> list[EvidenciaAdjunto]:
    """Resume fragmentos con localizadores estables para la ficha del caso."""
    evidence: list[EvidenciaAdjunto] = []
    current_location = "Contenido extraído"
    excerpt: list[str] = []

    def flush() -> None:
        value = " ".join(part.strip() for part in excerpt if part.strip()).strip()
        if value and len(evidence) < MAX_EVIDENCE_ITEMS:
            evidence.append(
                {"topic": "extracto", "value": "", "location": current_location, "excerpt": value[:MAX_EVIDENCE_CHARS]}
            )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        location, content = _evidence_location(line, current_location)
        if location != current_location:
            flush()
            excerpt.clear()
            current_location = location
        if content:
            excerpt.append(content)
    flush()
    return evidence


_PAGINAS = re.compile(r"P[áa]gina \d+(?:\s*[,;]\s*P[áa]gina \d+)+")


def _rangos(paginas: list[int]) -> str:
    """3, 5, 6, 7, 10 → «3, 5–7, 10»: las páginas consecutivas se agrupan en un rango."""
    partes: list[str] = []
    inicio = anterior = paginas[0]
    for numero in paginas[1:] + [0]:
        if numero == anterior + 1:
            anterior = numero
            continue
        partes.append(str(inicio) if inicio == anterior else
                      f"{inicio}, {anterior}" if anterior == inicio + 1 else f"{inicio}–{anterior}")
        inicio = anterior = numero
    return ", ".join(partes)


def compactar_paginas(texto: str) -> str:
    """Muestra una lista de «Página N, Página M…» como «págs. N, M–P»; no pierde ninguna página."""
    def reemplazo(coincidencia: re.Match[str]) -> str:
        paginas = sorted({int(n) for n in re.findall(r"\d+", coincidencia.group(0))})
        return f"págs. {_rangos(paginas)}"
    return _PAGINAS.sub(reemplazo, texto)
