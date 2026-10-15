"""Contenedores ZIP: apertura acotada, OCR de imágenes incrustadas y shapefiles comprimidos.

Es la base de los lectores de Office/OpenDocument (que son ZIP): ningún lector abre un ZIP sin pasar por `zip_acotado`.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from anka.dominio.attachments import MAX_ARCHIVE_EXPANDED_BYTES, MAX_ARCHIVE_FILES, MAX_EVIDENCE_ITEMS, MAX_OCR_IMAGES
from anka.dominio.lectores_shapefile import shapefile_text
from anka.dominio.lectores_texto import Unreadable
from anka.infra.lectores_imagen import ocr_image


def zip_acotado(content: bytes) -> zipfile.ZipFile:
    """Abre un ZIP (o un documento Office/OpenDocument, que lo es) rechazando bombas de descompresión.

    Los tamaños se leen del directorio central sin descomprimir nada; `zipfile` no lee más de lo declarado,
    así que el tope vale aunque la cabecera mienta. Sin esto, un adjunto de 10 MB podría expandirse a gigabytes.
    """
    archive = zipfile.ZipFile(BytesIO(content))
    entries = [item for item in archive.infolist() if not item.is_dir()]
    if len(entries) > MAX_ARCHIVE_FILES or sum(item.file_size for item in entries) > MAX_ARCHIVE_EXPANDED_BYTES:
        archive.close()
        raise Unreadable("el archivo excede el límite de entradas o de tamaño expandido; requiere revisión manual")
    return archive


def append_archive_image_ocr(archive: zipfile.ZipFile, images: list[str], lines: list[str]) -> None:
    """Agrega el OCR de las imágenes incrustadas de un archivo ZIP (con tope de MAX_OCR_IMAGES)."""
    for image_name in images[:MAX_OCR_IMAGES]:
        try:
            lines.append(f"[Imagen incrustada {image_name}; texto OCR]\n{ocr_image(archive.read(image_name))}")
            lines.append(
                f"COBERTURA VISUAL PARCIAL: el texto de {image_name} se reconoció por OCR; "
                "relaciones de tabla/figura no interpretadas automáticamente"
            )
        except Unreadable as exc:
            lines.append(f"CONTENIDO GRÁFICO NO INSPECCIONADO ({image_name}): {exc}")
    if len(images) > MAX_OCR_IMAGES:
        lines.append(f"LECTURA PARCIAL: documento con más de {MAX_OCR_IMAGES} imágenes incrustadas")


def shapefile_zip_text(content: bytes) -> str:
    """Describe las capas de shapefiles comprimidos en un ZIP: geometría, campos, extensión, registros y proyección."""
    with zip_acotado(content) as archive:
        groups: dict[str, dict[str, str]] = {}
        for name in archive.namelist():
            suffix = Path(name).suffix.casefold()
            if suffix in {".shp", ".shx", ".dbf", ".prj", ".cpg"}:
                groups.setdefault(str(Path(name).with_suffix("")), {})[suffix] = name
        fragments = []
        for layer, files in groups.items():
            if ".shp" not in files:
                continue
            if not {".shx", ".dbf"}.issubset(files):     # incompleto: se lee lo que haya y se marca lo que falta
                fragments.append(shapefile_text(
                    layer, archive.read(files[".shp"]),
                    {suffix: archive.read(name) for suffix, name in files.items() if suffix != ".shp"}))
                continue
            try:
                import shapefile
            except ImportError:
                raise Unreadable("falta instalar pyshp para leer shapefiles") from None
            reader = shapefile.Reader(
                shp=BytesIO(archive.read(files[".shp"])),
                shx=BytesIO(archive.read(files[".shx"])),
                dbf=BytesIO(archive.read(files[".dbf"])),
            )
            fields = [field[0] for field in reader.fields[1:]]
            fragments.append(
                f"Capa shapefile: {layer}; geometría: {reader.shapeTypeName}; "
                f"elementos: {len(reader)}; campos: {', '.join(fields)}"
            )
            if reader.bbox:
                fragments.append("Extensión XY (mín X, mín Y, máx X, máx Y): " + ", ".join(map(str, reader.bbox)))
            for index, record in enumerate(reader.iterRecords()):
                if index >= MAX_EVIDENCE_ITEMS:
                    fragments.append(f"LECTURA PARCIAL: se muestran {MAX_EVIDENCE_ITEMS} registros de atributos")
                    break
                fragments.append(
                    f"Registro {index + 1}: "
                    + "; ".join(f"{field}={value}" for field, value in zip(fields, record, strict=True))
                )
            if ".prj" in files:
                projection = archive.read(files[".prj"]).decode("utf-8", errors="replace")
                fragments.append("Sistema de coordenadas (PRJ): " + projection)
            else:
                fragments.append("SISTEMA DE COORDENADAS NO DECLARADO: el shapefile no incluye archivo .prj")
            reader.close()
        if not fragments:
            raise Unreadable("ZIP sin archivos .shp que leer")
        return "\n".join(fragments)
