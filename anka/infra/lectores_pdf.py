"""Lector de PDF: texto nativo y OCR de las páginas escaneadas o con contenido visual."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from anka.dominio.attachments import (
    FRACCION_DE_PDF_ESCANEADO, MAX_OCR_PAGES, MAX_OCR_PIXELS, MAX_PDF_PAGINAS_LEIDAS, MIN_CARACTERES_POR_PAGINA,
    rangos_de_paginas,
)
from anka.dominio.lectores_texto import (
    Unreadable, hay_tabla_en_imagen, lineas_nuevas_del_ocr, reparar_ligaduras_perdidas,
)
from anka.infra.lectores_imagen import ocr_pil_image
from anka.infra.presupuesto_ocr import OcrOmitidoPorTiempo, presupuesto_actual, presupuesto_de_archivo

AVISO_TABLA_EN_IMAGEN = (
    "TABLA EN IMAGEN: las cifras vienen del OCR; el orden puede no corresponder a sus filas y columnas y pueden "
    "faltar celdas. Compruébalas en la imagen antes de usarlas"
)


def _paginas_en_texto(paginas: list[int]) -> str:
    return rangos_de_paginas(sorted(paginas))


def _ocr_de_la_pagina(pdf_page: Any, texto: str, tiene_contenido_visual: bool) -> tuple[str, bool]:
    """Hace OCR a una página renderizada; devuelve (texto con el OCR, si aún tiene contenido visual sin interpretar)."""
    width, height = pdf_page.get_size()
    if width * height * 9 > MAX_OCR_PIXELS:
        raise Unreadable("página PDF excede el límite de píxeles para OCR")
    bitmap = pdf_page.render(scale=3)
    image = bitmap.to_pil()
    try:
        ocr = ocr_pil_image(image)
    finally:
        image.close()
        bitmap.close()
    unico = lineas_nuevas_del_ocr(ocr, texto)
    if unico:
        texto = (texto.rstrip() + "\n[OCR visual adicional]\n" + "\n".join(unico)).strip()
    if hay_tabla_en_imagen(unico):
        texto += f"\n{AVISO_TABLA_EN_IMAGEN}"
    if tiene_contenido_visual:
        texto += (
            "\nCOBERTURA VISUAL PARCIAL: OCR de la página ejecutado; "
            "relaciones de tabla/figura requieren interpretación visual"
        )
    return texto, False


def _avisos_de_cobertura(total: int, leidas: int, sin_ocr: dict[str, list[int]], escaneadas: int) -> list[str]:
    """Líneas finales que dicen qué páginas no se leyeron o no pasaron por OCR, y por qué (U-55)."""
    avisos = []
    if total > leidas:
        avisos.append(
            f"COBERTURA PARCIAL: PDF de {total} páginas; se leyeron las páginas 1–{leidas} (tope de lectura "
            f"{MAX_PDF_PAGINAS_LEIDAS}); faltan las páginas {leidas + 1}–{total}"
        )
    if sin_ocr["politica"]:
        avisos.append(
            f"REQUIERE VISIÓN: {escaneadas} de {leidas} páginas parecen escaneadas (poco o nada de texto); OCR local "
            f"omitido por la política ocr_omitir_escaneados; páginas sin OCR: {_paginas_en_texto(sin_ocr['politica'])}"
        )
    if sin_ocr["tiempo"]:
        presupuesto = presupuesto_actual()
        avisos.append(
            f"COBERTURA PARCIAL: OCR omitido por tiempo (presupuesto {presupuesto.descripcion()}); "
            f"páginas sin OCR: {_paginas_en_texto(sin_ocr['tiempo'])}"
        )
    if sin_ocr["tope"]:
        avisos.append(
            f"COBERTURA PARCIAL: OCR limitado a {MAX_OCR_PAGES} páginas por archivo; "
            f"páginas sin OCR: {_paginas_en_texto(sin_ocr['tope'])}"
        )
    return avisos


def pdf_text(content: bytes) -> str:
    """Texto de un PDF por página (ver `_texto_del_pdf`); el tiempo de OCR cuenta contra el presupuesto del archivo."""
    with presupuesto_de_archivo():
        return _texto_del_pdf(content)


def _textos_nativos(reader: Any, leidas: int) -> tuple[list[str], dict[int, int]]:
    """El texto nativo de cada página con las sílabas «ti» reparadas, y cuántas se repararon por página (T-124).

    El aviso no se añade aquí: no debe contar como texto al decidir si una página parece escaneada.
    """
    nativos = []
    ligaduras: dict[int, int] = {}
    for numero in range(leidas):
        texto, recuperadas = reparar_ligaduras_perdidas(reader.pages[numero].extract_text() or "")
        nativos.append(texto)
        if recuperadas:
            ligaduras[numero + 1] = recuperadas
    return nativos, ligaduras


def _con_aviso_de_ligaduras(texto: str, recuperadas: int) -> str:
    if not recuperadas:
        return texto
    return texto + (f"\nLIGADURAS RECUPERADAS: {recuperadas} «ti» restauradas de caracteres no legibles "
                    "(reparación por heurística)")


def _texto_del_pdf(content: bytes) -> str:
    """Texto de un PDF por página; usa OCR en las páginas escaneadas o con contenido visual.

    Lee hasta `MAX_PDF_PAGINAS_LEIDAS` páginas (un PDF más largo se lee parcialmente, no se rechaza) y respeta el
    presupuesto de segundos de OCR y la política `ocr_omitir_escaneados`; lo que no pasa por OCR se dice con sus
    páginas.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unreadable("falta instalar pypdf para leer PDF") from None
    reader = PdfReader(BytesIO(content))
    total = len(reader.pages)
    leidas = min(total, MAX_PDF_PAGINAS_LEIDAS)
    try:
        import pypdfium2 as pdfium

        pdf_document = pdfium.PdfDocument(content)
    except ImportError:
        pdfium = None
        pdf_document = None
    nativos, ligaduras = _textos_nativos(reader, leidas)
    escaneadas = [n for n, texto in enumerate(nativos, 1) if len(texto.strip()) < MIN_CARACTERES_POR_PAGINA]
    omitir_todo = (presupuesto_actual().omitir_escaneados and bool(escaneadas)
                   and len(escaneadas) / leidas >= FRACCION_DE_PDF_ESCANEADO)
    sin_ocr: dict[str, list[int]] = {"politica": [], "tiempo": [], "tope": []}
    con_ocr = 0
    page_texts: list[tuple[str, bool]] = []
    for page_number, text in enumerate(nativos, 1):
        pdf_page = pdf_document[page_number - 1] if pdf_document is not None else None
        has_visual_content = _pdf_visual_content(pdf_page, pdfium)
        needs_ocr = not text.strip() or len(text.strip()) < MIN_CARACTERES_POR_PAGINA or has_visual_content
        if needs_ocr and pdf_page is not None:
            if omitir_todo and page_number in escaneadas:
                sin_ocr["politica"].append(page_number)
                has_visual_content = True
            elif con_ocr >= MAX_OCR_PAGES:
                sin_ocr["tope"].append(page_number)
                has_visual_content = True
            else:
                try:
                    con_ocr += 1
                    text, has_visual_content = _ocr_de_la_pagina(pdf_page, text, has_visual_content)
                except OcrOmitidoPorTiempo:
                    sin_ocr["tiempo"].append(page_number)
                    has_visual_content = True
                except Unreadable as exc:
                    text = (text.rstrip() + f"\nCONTENIDO VISUAL NO INSPECCIONADO: {exc}").strip()
                    has_visual_content = True
        elif needs_ocr:
            message = "\nCONTENIDO VISUAL NO INSPECCIONADO: falta pypdfium2 para renderizar la página"
            text = (text.rstrip() + message).strip()
            has_visual_content = True
        if pdf_page is not None:
            pdf_page.close()
        page_texts.append((_con_aviso_de_ligaduras(text, ligaduras.get(page_number, 0)), has_visual_content))
    if pdf_document is not None:
        pdf_document.close()
    avisos = _avisos_de_cobertura(total, leidas, sin_ocr, len(escaneadas))
    if not any(page.strip() for page, _ in page_texts):
        if avisos:
            motivo = avisos[0].replace("COBERTURA PARCIAL: ", "").replace("REQUIERE VISIÓN: ", "requiere visión: ")
            raise Unreadable(motivo)
        raise Unreadable("PDF sin texto extraíble ni texto legible por OCR")
    cuerpo = "\n".join(
        f"[Página {number}{'; sin texto extraíble' if not page.strip() else ''}"
        f"{'; contiene imágenes no interpretadas por OCR' if has_images else ''}]\n{page}"
        for number, (page, has_images) in enumerate(page_texts, 1)
    )
    return "\n".join([cuerpo, *avisos])


def _pdf_visual_content(pdf_page: Any, pdfium: Any) -> bool:
    has_visual_content = False
    if pdf_page is not None:
        try:
            page_width, page_height = pdf_page.get_size()
            page_area = page_width * page_height
            image_objects = pdf_page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE, pdfium.raw.FPDF_PAGEOBJ_PATH])
            for image_object in image_objects:
                left, bottom, right, top = image_object.get_bounds()
                image_width, image_height = max(0, right - left), max(0, top - bottom)
                image_area = image_width * image_height
                if image_area >= page_area * 0.025 or (image_width >= page_width * 0.35 and image_height >= 65):
                    has_visual_content = True
                    break
        except Exception as exc:
            raise Unreadable(f"inspección visual PDF falló ({type(exc).__name__}); revisión interna") from exc
    return has_visual_content
