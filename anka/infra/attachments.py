"""Lectores de documentos y archivos comprimidos y coordinación de extracción."""

from __future__ import annotations
import posixpath
from anka.dominio.attachments import (
    attachment_reviewed_sections as attachment_reviewed_sections,
    build_attachment_evidence as build_attachment_evidence,
)
import re
from collections.abc import Mapping
from pathlib import Path
from anka.dominio.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_WORK_PACKET_ATTACHMENT_BYTES,
    MAX_ARCHIVE_FILES,
    MAX_ARCHIVE_EXPANDED_BYTES,
    MARCA_FORMATO_ABIERTO,
    TEXT_ATTACHMENT_EXTENSIONS,
    motivo_sin_lector,
)
from anka.dominio.lectores_cad import dxf_text
from anka.dominio.lectores_shapefile import dbf_text, shapefile_text
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
from anka.infra.lectores_zip import shapefile_zip_text, zip_acotado
from anka.infra.lectores_imagen import image_text, office_legacy_text
from anka.infra.presupuesto_ocr import presupuesto_de_archivo


def _zip_text(content: bytes) -> str:
    """Inspecciona paquetes ZIP sin extraerlos a disco; limita expansión y cantidad de entradas."""
    with zip_acotado(content, "paquete ZIP") as archive:
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
            ".dxf",
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
            sin_lector = [name for name in unsupported if motivo_sin_lector(Path(name).suffix.casefold())]
            if sin_lector:
                fragments.append(f"Sin lector local: {', '.join(sin_lector[:100])}; {MARCA_FORMATO_ABIERTO}")
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
    return ""


def _read_by_format(filename: str, mime_type: str, content: bytes,
                    companeros: Mapping[str, bytes] | None = None) -> str:
    extension = Path(filename).suffix.casefold()
    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}:
        return image_text(filename, content)
    if extension == ".xls":
        return xls_text(content)
    if extension in {".doc", ".ppt"}:
        return office_legacy_text(extension, content)
    if extension == ".shp":
        return shapefile_text(Path(filename).stem, content, companeros)
    if extension == ".dbf":
        return dbf_text(Path(filename).stem, content, (companeros or {}).get(".cpg"))
    if extension == ".dxf":
        return dxf_text(Path(filename).name, content)
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
            ".dbf",
            ".dxf",
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


# Extraer texto por OCR no certifica la interpretación de tablas y figuras (T-24, T-45).
_MARCAS_DE_LECTURA_PARCIAL = (
    "COBERTURA VISUAL PARCIAL",
    "LECTURA PARCIAL",
    "CONTENIDO VISUAL NO INSPECCIONADO",
    "CONTENIDO GRÁFICO",
    "sin texto alternativo; requiere inspección visual",
    "[Imagen:",
    "contiene imágenes no interpretadas por OCR",
    "CONTENIDO DE GRÁFICOS NO EXTRAÍDO",
    "COBERTURA PARCIAL:",
    "CAMBIOS RASTREADOS:",
)


def _motivos_de_lectura_parcial(text: str) -> list[str]:
    """Por qué la lectura de un archivo legible quedó incompleta; vacío si se leyó por completo."""
    reasons = []
    blank_pages = re.findall(r"\[Página (\d+); sin texto extraíble", text)
    if blank_pages:
        reasons.append("páginas sin texto extraíble (posible escaneo u hoja vacía): " + ", ".join(blank_pages))
    if any(marker in text for marker in _MARCAS_DE_LECTURA_PARCIAL):
        reasons.append("hay imágenes, gráficos, comentarios o secciones que requieren inspección visual/manual")
    if "LECTURA PARCIAL DEL ZIP" in text:
        reasons.append("uno o más archivos dentro del ZIP no se pudieron extraer por completo")
    if "REQUIERE VISIÓN" in text:
        reasons.append("requiere visión: páginas escaneadas sin OCR local; que las lea Work o una persona")
    if "OCR omitido por tiempo" in text or "OCR OMITIDO POR TIEMPO" in text:
        reasons.append("OCR omitido por tiempo: se agotó el presupuesto de segundos de OCR; lectura parcial")
    if "SISTEMA DE COORDENADAS NO DECLARADO" in text:
        reasons.append("shapefile sin .prj; revisar referencia espacial antes de interpretar coordenadas")
    return reasons


def _leer_adjunto(filename: str, mime_type: str, content: bytes, max_bytes: int, detalle_del_error: bool,
                  companeros: Mapping[str, bytes] | None = None) -> tuple[str, str]:
    """Texto completo y motivo de lectura parcial. `detalle_del_error=False` no repite el texto de la excepción."""
    if len(content) > max_bytes:
        return "", f"supera el límite de {max_bytes // (1024 * 1024)} MiB"
    extension = Path(filename).suffix.casefold()
    if sin_lector := motivo_sin_lector(extension, content):
        return "", sin_lector
    if not _readable(filename, mime_type):
        return "", f"formato no soportado ({extension or mime_type or 'desconocido'})"
    try:
        with presupuesto_de_archivo():
            text = _read_by_format(filename, mime_type, content, companeros).strip()
    except Unreadable as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"no se pudo leer: {exc}" if detalle_del_error else f"no se pudo leer ({type(exc).__name__})"
    if not text:
        return "", "el archivo no contiene texto extraíble"
    reasons = _motivos_de_lectura_parcial(text)
    return text, ("lectura parcial: " + "; ".join(reasons)) if reasons else ""


def extract_attachment_text(filename: str, mime_type: str, content: bytes,
                            companeros: Mapping[str, bytes] | None = None) -> tuple[str, str]:
    """Devuelve el texto completo y el motivo si quedó algo sin leer; el texto mantiene páginas, filas o elementos.

    `companeros` son los archivos hermanos de un `.shp` suelto (`.shx`, `.dbf`, `.prj`, `.cpg`), por extensión.
    """
    return _leer_adjunto(filename, mime_type, content, MAX_ATTACHMENT_BYTES, True, companeros)


def extract_attachment_text_for_work_packet(filename: str, mime_type: str, content: bytes,
                                            companeros: Mapping[str, bytes] | None = None) -> tuple[str, str]:
    """Igual que `extract_attachment_text` para el paquete que se adjunta a Work (sin repetir contenido en errores)."""
    return _leer_adjunto(filename, mime_type, content, MAX_WORK_PACKET_ATTACHMENT_BYTES, False, companeros)


