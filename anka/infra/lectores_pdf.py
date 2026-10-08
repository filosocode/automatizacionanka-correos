"""Lector de PDF: texto nativo y OCR de las páginas escaneadas o con contenido visual."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from anka.dominio.attachments import MAX_OCR_PAGES, MAX_OCR_PIXELS
from anka.dominio.lectores_texto import Unreadable
from anka.infra.lectores_imagen import ocr_pil_image


def pdf_text(content: bytes) -> str:
    """Texto de un PDF por página; usa OCR en las páginas escaneadas o con contenido visual."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unreadable("falta instalar pypdf para leer PDF") from None
    reader = PdfReader(BytesIO(content))
    page_texts: list[tuple[str, bool]] = []
    if len(reader.pages) > MAX_OCR_PAGES:
        raise Unreadable(f"PDF supera el límite de {MAX_OCR_PAGES} páginas; requiere revisión manual")
    try:
        import pypdfium2 as pdfium

        pdf_document = pdfium.PdfDocument(content)
    except ImportError:
        pdfium = None
        pdf_document = None
    for page_number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        pdf_page = pdf_document[page_number - 1] if pdf_document is not None else None
        has_visual_content = _pdf_visual_content(pdf_page, pdfium, page_texts)
        needs_ocr = not text.strip() or len(text.strip()) < 100 or has_visual_content
        if needs_ocr and pdf_page is not None:
            try:
                bitmap = pdf_page.render(scale=3)
                image = bitmap.to_pil()
                if image.width * image.height > MAX_OCR_PIXELS:
                    raise Unreadable("página PDF excede el límite de píxeles para OCR")
                ocr = ocr_pil_image(image)
                native_compact = re.sub(r"\W+", "", text.casefold())
                unique_ocr = [
                    line
                    for line in ocr.splitlines()
                    if re.sub(r"\W+", "", line.casefold()) and re.sub(r"\W+", "", line.casefold()) not in native_compact
                ]
                if unique_ocr:
                    text = (text.rstrip() + "\n[OCR visual adicional]\n" + "\n".join(unique_ocr)).strip()
                if has_visual_content:
                    text += (
                        "\nCOBERTURA VISUAL PARCIAL: OCR de la página ejecutado; "
                        "relaciones de tabla/figura requieren interpretación visual"
                    )
                has_visual_content = False
            except Unreadable as exc:
                text = (text.rstrip() + f"\nCONTENIDO VISUAL NO INSPECCIONADO: {exc}").strip()
                has_visual_content = True
        elif needs_ocr:
            message = "\nCONTENIDO VISUAL NO INSPECCIONADO: falta pypdfium2 para renderizar la página"
            text = (text.rstrip() + message).strip()
            has_visual_content = True
        if pdf_page is not None:
            pdf_page.close()
        page_texts.append((text, has_visual_content))
    if pdf_document is not None:
        pdf_document.close()
    if not any(page.strip() for page, _ in page_texts):
        raise Unreadable("PDF sin texto extraíble ni texto legible por OCR")
    return "\n".join(
        f"[Página {number}{'; sin texto extraíble' if not page.strip() else ''}"
        f"{'; contiene imágenes no interpretadas por OCR' if has_images else ''}]\n{page}"
        for number, (page, has_images) in enumerate(page_texts, 1)
    )


def _pdf_visual_content(pdf_page: Any, pdfium: Any, page_texts: list[tuple[str, bool]]) -> bool:
    has_visual_content = False
    if pdf_page is not None:
        try:
            page_width, page_height = pdf_page.get_size()
            page_area = page_width * page_height
            image_objects = pdf_page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])
            for image_object in image_objects:
                left, bottom, right, top = image_object.get_bounds()
                image_width, image_height = max(0, right - left), max(0, top - bottom)
                image_area = image_width * image_height
                if image_area >= page_area * 0.025 or (image_width >= page_width * 0.35 and image_height >= 65):
                    has_visual_content = True
                    break
        except Exception as exc:
            page_texts.append((f"COBERTURA PARCIAL: inspección visual PDF falló ({type(exc).__name__})", True))
            has_visual_content = True
    return has_visual_content
