"""Contenido de la carpeta de la solicitud: adjuntos PDF/geográficos del cliente y tabla en Sheet."""

import _isolate  # noqa: F401  (debe ir primero)

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import case_actions
import drive_contents
import settings
import store
from fake_drive import FakeDriveTree
from status import QuoteStatus
from test_app import FakeGmail, complete_analysis, encoded_message, test_config

OWN = {"ankacomercialia@gmail.com"}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def part(filename, mime, attachment_id=None, data=None, inline=False):
    body = {"attachmentId": attachment_id} if attachment_id else {"data": b64(data or b"")}
    headers = [{"name": "Content-ID", "value": "<logo>"}] if inline else []
    return {"filename": filename, "mimeType": mime, "body": body, "headers": headers}


def make_thread():
    first = encoded_message("m-1", "Cliente <cliente@example.com>", "Solicito cotización.", 1, parts=[
        part("alcance.pdf", "application/pdf", "att-pdf"),
        part("predio.KMZ", "application/vnd.google-earth.kmz", "att-kmz"),
        part("tdr.docx", "application/octet-stream", "att-docx"),
        part("logo.png", "image/png", "att-logo", inline=True),
    ])
    ours = encoded_message("m-2", "Anka <ankacomercialia@gmail.com>", "Preguntas.", 2, parts=[
        part("borrador-interno.pdf", "application/pdf", "att-own"),
    ])
    again = encoded_message("m-3", "Cliente <cliente@example.com>", "Aquí el plano.", 3, parts=[
        part("plano.dwg", "application/acad", data=b"DWGDATA"),
    ])
    data = {
        ("m-1", "att-pdf"): b64(b"%PDF-alcance"), ("m-1", "att-kmz"): b64(b"KMZDATA"),
        ("m-1", "att-docx"): b64(b"DOCX"), ("m-1", "att-logo"): b64(b"PNG"),
        ("m-2", "att-own"): b64(b"PROPIO"),
    }
    return {"id": "t-1", "messages": [first, ours, again]}, data


def quote():
    return {"thread_id": "t-1", "client_name": "Minera Sur", "status": "ready",
            "analysis": complete_analysis()}


class PopulateTests(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDriveTree({"Minera Sur": {"Solicitud": {}}})
        self.folder = self.drive.folder_id("Minera Sur/Solicitud")
        self.thread, data = make_thread()
        self.gmail = FakeGmail(self.thread, data)

    def run_populate(self):
        return drive_contents.populate_project_folder(
            self.gmail, self.drive, self.thread, self.folder, quote(), OWN)

    def uploads(self):
        return {name: (mime, data) for _, name, mime, data in self.drive.uploads}

    def test_copies_everything_the_client_sent_and_creates_the_sheet(self):
        report = self.run_populate()
        uploads = self.uploads()
        self.assertEqual(uploads["alcance.pdf"], ("", b"%PDF-alcance"))
        self.assertEqual(uploads["predio.KMZ"][1], b"KMZDATA")
        self.assertEqual(uploads["plano.dwg"][1], b"DWGDATA")  # contenido en el propio mensaje
        sheet = [(n, m) for n, (m, _) in uploads.items() if n.startswith("Tabla de actividades")]
        self.assertEqual(sheet, [("Tabla de actividades - Proyecto Norte", drive_contents.SHEET_MIME)])
        self.assertEqual(report.table, "creada")
        self.assertEqual(uploads["tdr.docx"][1], b"DOCX")  # Word, Excel, etc. también
        self.assertEqual(sorted(report.copied), ["alcance.pdf", "plano.dwg", "predio.KMZ", "tdr.docx"])

    def test_skips_only_signature_logos_and_our_own_attachments(self):
        report = self.run_populate()
        self.assertNotIn("logo.png", self.uploads())
        self.assertNotIn("borrador-interno.pdf", self.uploads())
        self.assertNotIn("logo.png", report.copied)

    def test_running_twice_does_not_duplicate_anything(self):
        self.run_populate()
        count = len(self.drive.uploads)
        report = self.run_populate()
        self.assertEqual(len(self.drive.uploads), count)
        self.assertEqual(report.copied, [])
        self.assertEqual(report.table, "ya existía")
        self.assertEqual(sorted(report.existing), ["alcance.pdf", "plano.dwg", "predio.KMZ", "tdr.docx"])

    def test_an_upload_failure_is_reported_and_does_not_stop_the_rest(self):
        original = drive_contents._upload

        def flaky(drive, folder_id, name, content, mime, target_mime=None):
            if name == "alcance.pdf":
                raise RuntimeError("cuota")
            return original(drive, folder_id, name, content, mime, target_mime)

        with patch.object(drive_contents, "_upload", flaky), self.assertLogs("anka.drive_contents", "ERROR"):
            report = self.run_populate()
        self.assertEqual(report.failed, ["alcance.pdf"])
        self.assertIn("predio.KMZ", report.copied)
        self.assertEqual(report.table, "creada")
        self.assertIn("No pude copiar: alcance.pdf", report.summary())

    def test_a_thread_without_attachments_only_gets_the_table(self):
        thread = {"id": "t-1", "messages": [encoded_message("m-1", "c@example.com", "Hola", 1)]}
        report = drive_contents.populate_project_folder(
            FakeGmail(thread), self.drive, thread, self.folder, quote(), OWN)
        self.assertEqual(report.copied, [])
        self.assertEqual(report.table, "creada")


class FolderCreationFillsItTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.drive = FakeDriveTree({"Minera Sur": {}})
        self.thread, data = make_thread()
        self.gmail = FakeGmail(self.thread, data)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("case_actions.load_config", return_value=test_config()),
                  patch("case_actions.services", side_effect=lambda: (self.gmail, self.drive))):
            p.start()
            self.addCleanup(p.stop)
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-1", "m-3", QuoteStatus.READY_REVIEW, "cliente@example.com",
                   complete_analysis() | {"received_at": "2026-09-01T09:00:00-05:00"})
        db.execute("INSERT INTO quote_metadata (thread_id, client_name, updated_at) VALUES ('t-1', 'Minera Sur', 'x')")
        db.commit()
        db.close()

    def test_approving_the_folder_also_fills_it(self):
        message = case_actions.approve_review("t-1")
        names = {name for _, name, _, _ in self.drive.uploads}
        self.assertIn("alcance.pdf", names)
        self.assertTrue(any(n.startswith("Tabla de actividades") for n in names))
        self.assertIn("En la carpeta", message)
        self.assertEqual({parent for parent, *_ in self.drive.uploads}, {self.drive.folder_id(
            "Minera Sur/" + next(f["name"] for f in self.drive.children(self.drive.folder_id("Minera Sur"))))})

    def test_a_failure_filling_the_folder_does_not_undo_the_folder(self):
        with patch("case_actions.populate_project_folder", side_effect=RuntimeError("boom")), \
                self.assertLogs("anka.case_actions", "ERROR"):
            message = case_actions.approve_review("t-1")
        self.assertIn("no pude copiar su contenido", message)
        import quotes
        self.assertEqual(quotes.get_quote("t-1")["status"], QuoteStatus.READY)


if __name__ == "__main__":
    unittest.main()
