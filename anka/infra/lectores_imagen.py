"""OCR y conversión externa de documentos antiguos."""

from __future__ import annotations
from typing import Any
from functools import lru_cache
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from anka.dominio.attachments import MAX_OCR_PIXELS
from anka.dominio.lectores_texto import Unreadable, plain_text


@lru_cache(maxsize=1)
def ocr_engine() -> Any:
    """Carga y reutiliza el motor OCR local."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise Unreadable("falta instalar rapidocr-onnxruntime para leer imágenes y PDF escaneados") from None
    return RapidOCR()


def ocr_image(content: bytes) -> str:
    """Reconoce texto de una imagen respetando el límite de píxeles."""
    try:
        from PIL import Image, ImageOps
        import numpy as np
    except ImportError:
        raise Unreadable("falta instalar Pillow y numpy para leer imágenes") from None
    try:
        with Image.open(BytesIO(content)) as source:
            if source.width * source.height > MAX_OCR_PIXELS:
                raise Unreadable("imagen excede el límite de píxeles para OCR; requiere revisión manual")
            image = ImageOps.exif_transpose(source).convert("RGB")
            return ocr_pil_image(image, np)
    except Unreadable:
        raise
    except Exception as exc:
        raise Unreadable(f"no se pudo ejecutar OCR sobre la imagen: {exc}") from None


def ocr_pil_image(image: Any, numpy_module: Any = None) -> str:
    """Reconoce una imagen PIL ya decodificada, como las páginas renderizadas de PDFium."""
    if image.width * image.height > MAX_OCR_PIXELS:
        raise Unreadable("imagen excede el límite de píxeles para OCR; requiere revisión manual")
    if numpy_module is None:
        try:
            import numpy as numpy_module
        except ImportError:
            raise Unreadable("falta instalar numpy para leer imágenes") from None
    try:
        result, _ = ocr_engine()(numpy_module.asarray(image.convert("RGB")))
    except Exception as exc:
        raise Unreadable(f"no se pudo ejecutar OCR sobre la imagen: {exc}") from None
    lines = []
    if result:
        for entry in result:
            if len(entry) >= 3 and float(entry[2]) >= 0.25 and str(entry[1]).strip():
                lines.append(str(entry[1]).strip())
    if not lines:
        raise Unreadable("OCR no detectó texto legible en la imagen; requiere inspección visual")
    return "\n".join(lines)


def image_text(filename: str, content: bytes) -> str:
    """Asocia el texto reconocido con el nombre de la imagen."""
    return f"[Imagen: {Path(filename).name}; texto OCR]\n{ocr_image(content)}"


def office_legacy_text(extension: str, content: bytes) -> str:
    """Convierte documentos antiguos mediante LibreOffice o Antiword con tiempo limitado."""
    converter = shutil.which("soffice") or shutil.which("libreoffice")
    if not converter and extension == ".doc":
        converter = shutil.which("antiword")
    if not converter:
        raise Unreadable(f"formato Office antiguo ({extension}); instale LibreOffice para convertirlo a texto")
    with tempfile.TemporaryDirectory(prefix="anka-adjunto-") as directory:
        source = Path(directory) / f"documento{extension}"
        source.write_bytes(content)
        if Path(converter).name.casefold().startswith("antiword"):
            result = subprocess.run([converter, str(source)], capture_output=True, timeout=60, check=False)
            if result.returncode:
                raise Unreadable("antiword no pudo convertir el documento Word antiguo")
            return plain_text(result.stdout)
        result = subprocess.run(
            [converter, "--headless", "--convert-to", "txt:Text", "--outdir", directory, str(source)],
            capture_output=True,
            timeout=120,
            check=False,
        )
        converted = source.with_suffix(".txt")
        if result.returncode or not converted.exists():
            raise Unreadable(f"LibreOffice no pudo convertir {extension} a texto")
        return plain_text(converted.read_bytes())
