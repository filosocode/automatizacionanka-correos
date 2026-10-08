"""Lectores de documentos y archivos comprimidos y coordinación de extracción."""

from __future__ import annotations
import posixpath
from anka.dominio.attachments import (
    attachment_reviewed_sections as attachment_reviewed_sections,
    build_attachment_evidence as build_attachment_evidence,
)
import re
import zipfile
from io import BytesIO
from pathlib import Path
from anka.dominio.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_TEXT,
    MAX_WORK_PACKET_ATTACHMENT_BYTES,
    MAX_ARCHIVE_FILES,
    MAX_ARCHIVE_EXPANDED_BYTES,
    TEXT_ATTACHMENT_EXTENSIONS,
)
from anka.dominio.lectores_texto import Unreadable, plain_text, html_text, rtf_text
from anka.dominio.lectores_geograficos import (
    kmz_text,
    kml_text_file,
    wpml_text,
    gpx_text,
    qgc_waypoints_text,
    qgc_plan_text,
    geojson_text,
    csv_text,
)
from anka.infra.lectores_office import docx_text, ods_text, odp_text, odt_text, pptx_text, xls_text, xlsx_text
from anka.infra.lectores_pdf import pdf_text
from anka.infra.lectores_zip import shapefile_zip_text
from anka.infra.lectores_imagen import image_text, office_legacy_text


def _zip_text(content: bytes) -> str:
    """Inspecciona paquetes ZIP sin extraerlos a disco; limita expansión y cantidad de entradas."""
    with zipfile.ZipFile(BytesIO(content)) as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        supported_extensions = TEXT_ATTACHMENT_EXTENSIONS | {
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".xlsm",
            ".ppt",
            ".pptx",
            ".odt",
            ".ods",
            ".odp",
            ".rtf",
            ".html",
            ".htm",
            ".kmz",
            ".shp",
        }
        shapefile_sidecars = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
        has_shapefiles = any(Path(item.filename).suffix.casefold() == ".shp" for item in entries)
        supported = [
            item
            for item in entries
            if Path(item.filename).suffix.casefold() in supported_extensions
            and Path(item.filename).suffix.casefold() != ".shp"
        ]
        if len(entries) > MAX_ARCHIVE_FILES or sum(item.file_size for item in entries) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise Unreadable("paquete ZIP excede el límite de archivos o tamaño expandido; requiere revisión manual")
        if not supported and not has_shapefiles:
            raise Unreadable("el paquete ZIP no contiene documentos de formatos legibles")
        fragments = [shapefile_zip_text(content)] if has_shapefiles else []
        unreadable = []
        unsupported = [
            item.filename
            for item in entries
            if Path(item.filename).suffix.casefold() not in supported_extensions
            and Path(item.filename).suffix.casefold() not in shapefile_sidecars
        ]
        for item in supported:
            safe_name = posixpath.normpath(item.filename)
            if safe_name.startswith("../") or safe_name.startswith("/"):
                continue
            nested = extract_attachment_text(safe_name, "", archive.read(item))
            text, reason = nested
            fragments.append(f"Archivo dentro de ZIP: {safe_name}\n{text or reason}")
            if reason:
                unreadable.append(f"{safe_name}: {reason}")
        if unreadable:
            fragments.append("LECTURA PARCIAL DEL ZIP; elementos que requieren revisión: " + " | ".join(unreadable))
        if unsupported:
            fragments.append(
                "LECTURA PARCIAL DEL ZIP; formatos internos no inspeccionados: " + ", ".join(unsupported[:100])
            )
        return "\n\n".join(fragments)


def _read_other_format(filename: str, mime_type: str, content: bytes, extension: str) -> str:
    """Extrae contenido y conserva localizadores para poder citar la fuente."""
    readers = {
        ".html": html_text,
        ".htm": html_text,
        ".rtf": rtf_text,
        ".gpx": gpx_text,
        ".waypoints": qgc_waypoints_text,
        ".plan": qgc_plan_text,
        ".geojson": geojson_text,
        ".csv": csv_text,
        ".pptx": pptx_text,
        ".odt": odt_text,
        ".ods": ods_text,
        ".odp": odp_text,
        ".zip": _zip_text,
    }
    if extension in readers:
        return readers[extension](content)
    if extension in {".kml", ".wpml"}:
        return {".kml": kml_text_file, ".wpml": wpml_text}[extension](Path(filename).name, content)
    if extension in TEXT_ATTACHMENT_EXTENSIONS or mime_type.startswith("text/"):
        return plain_text(content)
    if extension == ".kmz" or mime_type == "application/vnd.google-earth.kmz":
        return kmz_text(content)
    if extension == ".pdf" or mime_type == "application/pdf":
        return pdf_text(content)
    if extension == ".docx":
        return docx_text(content)
    if extension in {".xlsx", ".xlsm"}:
        return xlsx_text(content)
    if extension == ".dwg":
        raise Unreadable("DWG requiere exportación a PDF, DXF o imagen; revise el plano original")
    if extension in {".dwg", ".dxf"}:
        raise Unreadable("plano CAD requiere revisión manual")
    return ""


def _read_by_format(filename: str, mime_type: str, content: bytes) -> str:
    extension = Path(filename).suffix.casefold()
    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}:
        return image_text(filename, content)
    if extension == ".xls":
        return xls_text(content)
    if extension in {".doc", ".ppt"}:
        return office_legacy_text(extension, content)
    if extension == ".shp":
        raise Unreadable("shapefile incompleto; comprima .shp, .shx y .dbf juntos en un ZIP")
    return _read_other_format(filename, mime_type, content, extension)


def _readable(filename: str, mime_type: str) -> bool:
    extension = Path(filename).suffix.casefold()
    return (
        extension in TEXT_ATTACHMENT_EXTENSIONS
        or mime_type.startswith("text/")
        or extension
        in {
            ".kmz",
            ".pdf",
            ".docx",
            ".xlsx",
            ".xlsm",
            ".pptx",
            ".odt",
            ".ods",
            ".odp",
            ".rtf",
            ".html",
            ".htm",
            ".zip",
            ".xls",
            ".doc",
            ".ppt",
            ".shp",
            ".png",
            ".jpg",
            ".jpeg",
            ".tif",
            ".tiff",
            ".bmp",
            ".webp",
            ".gif",
        }
        or mime_type in {"application/vnd.google-earth.kmz", "application/pdf"}
    )


def extract_attachment_text(filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
    """Devuelve texto y motivo; el texto mantiene referencias a páginas, filas o elementos."""
    if len(content) > MAX_ATTACHMENT_BYTES:
        return "", f"supera el límite de {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"
    if not _readable(filename, mime_type):
        extension = Path(filename).suffix.casefold()
        if extension in {".dwg", ".dxf"}:
            return "", "plano CAD requiere revisión manual"
        return "", f"formato no soportado ({extension or mime_type or 'desconocido'})"
    reason = ""
    try:
        text = _read_by_format(filename, mime_type, content).strip()
    except Unreadable as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"no se pudo leer: {exc}"
    if not text:
        return "", "el archivo no contiene texto extraíble"
    blank_pages = re.findall(r"\[Página (\d+); sin texto extraíble", text)
    if blank_pages:
        reason = "lectura parcial: páginas sin texto extraíble (posible escaneo u hoja vacía): " + ", ".join(
            blank_pages
        )
    visual_limit = (
        "contiene imágenes no interpretadas por OCR" in text
        or "CONTENIDO GRÁFICO" in text
        or "LECTURA PARCIAL" in text
        or "sin texto alternativo; requiere inspección visual" in text
        or "[Imagen:" in text
        or "CONTENIDO VISUAL NO INSPECCIONADO" in text
        or "CONTENIDO DE GRÁFICOS NO EXTRAÍDO" in text
        or "COBERTURA VISUAL PARCIAL" in text
        or "COBERTURA PARCIAL:" in text
        or "CAMBIOS RASTREADOS:" in text
    )
    if visual_limit:
        reason = (
            "lectura parcial: hay imágenes, gráficos, comentarios o secciones que requieren inspección visual/manual"
        )
    if len(text) > MAX_ATTACHMENT_TEXT:
        return text[:MAX_ATTACHMENT_TEXT], (
            f"lectura parcial: se recortó a {MAX_ATTACHMENT_TEXT:,} caracteres "
            f"de {len(text):,}; revise el archivo completo"
        )
    if "LECTURA PARCIAL DEL ZIP" in text:
        reason = "lectura parcial: uno o más archivos dentro del ZIP no se pudieron extraer por completo"
    if "SISTEMA DE COORDENADAS NO DECLARADO" in text:
        reason = "lectura parcial: shapefile sin .prj; revisar referencia espacial antes de interpretar coordenadas"
    return text, reason


def extract_attachment_text_for_work_packet(filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
    """Extrae el texto completo disponible para un paquete local que se adjunta a Work.

    Usa los mismos lectores por formato, pero permite archivos mayores y no recorta a
    MAX_ATTACHMENT_TEXT. Los límites propios de cada lector (páginas, celdas y ZIP)
    siguen vigentes y se devuelven como motivo de lectura parcial.
    """
    if len(content) > MAX_WORK_PACKET_ATTACHMENT_BYTES:
        return "", (f"supera el límite del paquete local de {MAX_WORK_PACKET_ATTACHMENT_BYTES // (1024 * 1024)} MiB")
    if not _readable(filename, mime_type):
        extension = Path(filename).suffix.casefold()
        if extension in {".dwg", ".dxf"}:
            return "", "plano CAD requiere revisión manual"
        return "", f"formato no soportado ({extension or mime_type or 'desconocido'})"
    try:
        text = _read_by_format(filename, mime_type, content).strip()
    except Unreadable as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"no se pudo leer ({type(exc).__name__})"
    if not text:
        return "", "el archivo no contiene texto extraíble"

    reasons = []
    blank_pages = re.findall(r"\[Página (\d+); sin texto extraíble", text)
    if blank_pages:
        reasons.append("páginas sin texto extraíble: " + ", ".join(blank_pages))
    if any(
        marker in text
        for marker in (
            "LECTURA PARCIAL",
            "CONTENIDO VISUAL NO INSPECCIONADO",
            "CONTENIDO GRÁFICO",
            "sin texto alternativo; requiere inspección visual",
            "CONTENIDO DE GRÁFICOS NO EXTRAÍDO",
            "COBERTURA VISUAL PARCIAL",
            "COBERTURA PARCIAL:",
            "CAMBIOS RASTREADOS:",
        )
    ):
        reasons.append("hay contenido, gráficos o secciones que requieren inspección visual/manual")
    if "SISTEMA DE COORDENADAS NO DECLARADO" in text:
        reasons.append("shapefile sin .prj; revisar referencia espacial antes de interpretar coordenadas")
    return text, "; ".join(dict.fromkeys(reasons))


