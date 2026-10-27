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
MAX_OCR_PAGES = 200          # páginas de un PDF a las que se les hace OCR por archivo (las demás quedan «sin OCR»)
MAX_PDF_PAGINAS_LEIDAS = 600  # páginas de un PDF de las que se extrae el texto; más allá la cobertura es parcial (U-55)
MIN_CARACTERES_POR_PAGINA = 100   # menos texto nativo que esto: la página parece escaneada o casi vacía
FRACCION_DE_PDF_ESCANEADO = 0.5   # con esta fracción de páginas casi sin texto, el PDF se trata como escaneado
MIN_CARACTERES_DE_LINEA_REPETIDA = 20  # una línea de OCR más corta nunca se descarta por estar «contenida» (T-124)
MIN_CELDAS_SUELTAS_DE_TABLA = 5    # cifras cortas sueltas en el OCR de una página: huella de una tabla en imagen
MAX_CARACTERES_DE_CELDA = 7        # una cifra más larga (un teléfono de la firma) no es una celda de tabla
MARCA_FORMATO_ABIERTO = "pedir al cliente un formato abierto"
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
# Formatos sin lector local: no se lee nada y se le pide al cliente un formato abierto (U-55, punto 6).
_SIN_LECTOR = {
    ".rar": "archivo comprimido RAR",
    ".7z": "archivo comprimido 7z",
}
VERSIONES_DWG = {
    "AC1012": "R13", "AC1014": "R14", "AC1015": "2000", "AC1018": "2004", "AC1021": "2007",
    "AC1024": "2010", "AC1027": "2013", "AC1032": "2018",
}


EXTENSIONES_HERMANAS_SHP = (".shx", ".dbf", ".prj", ".cpg")


def hermanos_de_shapefile(nombres: list[str]) -> dict[str, dict[str, str]]:
    """{nombre del .shp: {extensión: nombre del archivo hermano}} entre los adjuntos recibidos.

    Hermano = mismo nombre base (sin distinguir mayúsculas) y extensión de shapefile. Un shapefile con sus hermanos
    cuenta como una sola entrada: los hermanos se leen junto con el `.shp` y no aparecen aparte (U-55).
    """
    por_base: dict[str, dict[str, str]] = {}
    for nombre in nombres:
        base, _, extension = nombre.rpartition(".")
        por_base.setdefault(base.casefold(), {})["." + extension.casefold()] = nombre
    return {grupo[".shp"]: {e: n for e, n in grupo.items() if e in EXTENSIONES_HERMANAS_SHP}
            for grupo in por_base.values() if ".shp" in grupo}


def motivo_sin_lector(extension: str, content: bytes = b"") -> str:
    """Motivo en español de un formato que no se puede leer, con la marca «pedir al cliente un formato abierto».

    Vacío si la extensión sí tiene lector (o es desconocida: ese caso lo explica quien llama).
    """
    if extension in _SIN_LECTOR:
        return f"{_SIN_LECTOR[extension]}: no hay lector local, no se vio su contenido; {MARCA_FORMATO_ABIERTO} (ZIP)"
    if extension == ".dwg":
        codigo = content[:6].decode("ascii", errors="replace")
        version = f" (AutoCAD {VERSIONES_DWG[codigo]}, {codigo})" if codigo in VERSIONES_DWG else ""
        return (f"plano DWG{version}: formato propietario sin lector local; "
                f"{MARCA_FORMATO_ABIERTO} (DXF, PDF o imagen)")
    return ""


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


def rangos_de_paginas(paginas: list[int]) -> str:
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
        return f"págs. {rangos_de_paginas(paginas)}"
    return _PAGINAS.sub(reemplazo, texto)
