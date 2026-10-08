"""Límites de los lectores de adjuntos: bombas de descompresión, archivos rotos y topes. Cubre: F1-03, R12, T-24"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import unittest
import zipfile
from unittest.mock import patch

from anka.infra import attachments, lectores_office, lectores_pdf, lectores_zip
from anka.infra.attachments import extract_attachment_text, extract_attachment_text_for_work_packet

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def zipped(files, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def docx(body="<w:p><w:r><w:t>Alcance</w:t></w:r></w:p>", extra=None):
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    return zipped({"word/document.xml": document, **(extra or {})}, zipfile.ZIP_DEFLATED)


class DecompressionBombTests(unittest.TestCase):
    """Un adjunto pequeño no puede obligar a descomprimir gigabytes: el tope se aplica antes de leer."""

    def bomb(self, member):
        # 4 MiB de ceros comprimen a unos pocos KiB; con el tope reducido equivalen a una bomba real.
        return zipped({member: b"0" * (4 * 1024 * 1024)}, zipfile.ZIP_DEFLATED)

    def test_office_documents_over_the_expansion_cap_are_refused_for_review(self):
        for name, member in (
            ("a.docx", "word/document.xml"),
            ("a.pptx", "ppt/slides/slide1.xml"),
            ("a.xlsx", "xl/workbook.xml"),
            ("a.odt", "content.xml"),
            ("a.ods", "content.xml"),
            ("a.odp", "content.xml"),
        ):
            with self.subTest(name=name), patch.object(lectores_zip, "MAX_ARCHIVE_EXPANDED_BYTES", 1024 * 1024):
                content = self.bomb(member)
                self.assertLess(len(content), 50_000)
                for reader in (extract_attachment_text, extract_attachment_text_for_work_packet):
                    text, reason = reader(name, "", content)
                    self.assertEqual(text, "")
                    self.assertIn("revisión manual", reason)

    def test_office_documents_with_too_many_entries_are_refused(self):
        many = zipped({f"word/media/i{n}.txt": b"x" for n in range(5)}, zipfile.ZIP_DEFLATED)
        with patch.object(lectores_zip, "MAX_ARCHIVE_FILES", 3):
            text, reason = extract_attachment_text("a.docx", DOCX, many)
        self.assertEqual(text, "")
        self.assertIn("revisión manual", reason)

    def test_a_normal_document_still_reads_under_the_cap(self):
        text, reason = extract_attachment_text("a.docx", DOCX, docx())
        self.assertIn("Párrafo 1: Alcance", text)
        self.assertEqual(reason, "")


class BrokenFileTests(unittest.TestCase):
    """Un archivo corrupto se informa como no legible y nunca rompe el ciclo de revisión."""

    def test_corrupt_files_return_a_reason_instead_of_raising(self):
        casos = {
            "roto.pdf": b"%PDF-1.4 truncado",
            "roto.docx": b"PK\x03\x04no es un zip",
            "roto.xlsx": b"esto no es un excel",
            "roto.pptx": zipped({"otra/cosa.xml": "<a/>"}),
            "roto.zip": b"PK\x05\x06" + b"\0" * 18 + b"basura",
            "vacio.pdf": b"",
            "vacio.docx": b"",
        }
        for name, content in casos.items():
            with self.subTest(name=name):
                for reader in (extract_attachment_text, extract_attachment_text_for_work_packet):
                    text, reason = reader(name, "", content)
                    self.assertEqual(text, "")
                    self.assertTrue(reason)

    def test_the_error_reason_of_the_work_packet_does_not_echo_file_content(self):
        _text, reason = extract_attachment_text_for_work_packet("roto.docx", DOCX, b"SECRETO-DEL-CLIENTE")
        self.assertNotIn("SECRETO", reason)


class SizeAndPageCapTests(unittest.TestCase):
    def test_gmail_sized_attachments_are_within_the_limit(self):
        self.assertGreaterEqual(attachments.MAX_ATTACHMENT_BYTES, 25 * 1024 * 1024)
        self.assertEqual(attachments.MAX_ATTACHMENT_BYTES, attachments.MAX_WORK_PACKET_ATTACHMENT_BYTES)

    def test_files_over_the_size_limit_are_not_read(self):
        with patch.object(attachments, "MAX_ATTACHMENT_BYTES", 10):
            text, reason = extract_attachment_text("a.txt", "text/plain", b"x" * 11)
        self.assertEqual(text, "")
        self.assertIn("supera el límite", reason)
        with patch.object(attachments, "MAX_WORK_PACKET_ATTACHMENT_BYTES", 10):
            text, reason = extract_attachment_text_for_work_packet("a.txt", "text/plain", b"x" * 11)
        self.assertEqual(text, "")
        self.assertIn("supera el límite", reason)

    def test_pdf_over_the_page_limit_requires_manual_review(self):
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(4):
            writer.add_blank_page(width=72, height=72)
        buffer = io.BytesIO()
        writer.write(buffer)
        with patch.object(lectores_pdf, "MAX_OCR_PAGES", 3):
            text, reason = extract_attachment_text("grande.pdf", "application/pdf", buffer.getvalue())
        self.assertEqual(text, "")
        self.assertIn("límite de 3 páginas", reason)

    def test_spreadsheet_over_the_cell_limit_is_reported_as_partial(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        for row in range(1, 21):
            sheet.append([f"A{row}", row, "x"])
        buffer = io.BytesIO()
        book.save(buffer)
        with patch.object(lectores_office, "MAX_SPREADSHEET_CELLS", 10):
            text, reason = extract_attachment_text("grande.xlsx", "", buffer.getvalue())
        self.assertIn("LECTURA PARCIAL", text)
        self.assertIn("parcial", reason)


class CompleteReadingTests(unittest.TestCase):
    """T-24: un archivo legible se lee completo; «parcial» solo si de verdad quedó contenido sin extraer."""

    OCR_PAGE = (
        "[Página 1]\n[OCR visual adicional]\nÁrea: 12 ha\n"
        "COBERTURA VISUAL PARCIAL: OCR de la página ejecutado; relaciones de tabla/figura requieren "
        "interpretación visual"
    )

    def classify(self, text):
        return attachments._motivos_de_lectura_parcial(text)

    def test_ocr_that_ran_does_not_downgrade_the_coverage(self):
        self.assertEqual(self.classify(self.OCR_PAGE), [])

    def test_content_that_could_not_be_extracted_is_still_partial(self):
        casos = [
            "[Página 3; sin texto extraíble (posible escaneo)]",
            "CONTENIDO GRÁFICO NO INSPECCIONADO (img1.png): sin OCR",
            "LECTURA PARCIAL: hoja Datos supera 50000 celdas inspeccionadas",
            "CONTENIDO DE GRÁFICOS NO EXTRAÍDO: ppt/charts/chart1.xml",
            "LECTURA PARCIAL DEL ZIP; elementos que requieren revisión: a.pdf",
            "SISTEMA DE COORDENADAS NO DECLARADO: sin .prj",
        ]
        for text in casos:
            with self.subTest(text=text[:40]):
                self.assertTrue(self.classify(text))

    def test_a_scanned_pdf_read_with_ocr_is_complete_in_every_mode(self):
        with patch.object(attachments, "_read_by_format", return_value=self.OCR_PAGE):
            for reader in (extract_attachment_text, extract_attachment_text_for_work_packet):
                text, reason = reader("e.pdf", "application/pdf", b"%PDF")
                self.assertIn("Área: 12 ha", text)
                self.assertEqual(reason, "")

    def test_the_partial_reasons_are_the_same_in_every_mode(self):
        with patch.object(attachments, "_read_by_format", return_value="[Página 3; sin texto extraíble]\nTexto"):
            self.assertEqual(extract_attachment_text("e.pdf", "application/pdf", b"x")[1],
                             extract_attachment_text_for_work_packet("e.pdf", "application/pdf", b"x")[1])


if __name__ == "__main__":
    unittest.main()
