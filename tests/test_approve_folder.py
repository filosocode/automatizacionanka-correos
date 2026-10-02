"""Aprobación humana de un caso con alcance completo: crea Año/Cliente/Solicitud."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.servicios import case_actions
from anka.servicios import quotes
from anka.infra import settings
from anka.infra import store
from anka.web import server as web
from anka.web import seguridad
from tests.fake_drive import FakeDriveTree
from anka.dominio.status import QuoteStatus
from tests.test_app import FakeGmail, complete_analysis, test_config


def ready_analysis(**extra):
    return complete_analysis() | {"received_at": "2026-09-01T09:00:00-05:00"} | extra


# Cubre: F1-07, R4, R6, R7
class ApproveFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.drive = FakeDriveTree({"Minera Sur S.A.S.": {}})
        for p in (
            patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
            patch("anka.servicios.case_actions.load_config", return_value=test_config()),
            patch("anka.servicios.case_actions.services", side_effect=lambda: (FakeGmail({"id": "t-1", "messages": []}), self.drive)),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.seed()

    def seed(self, status=QuoteStatus.READY_REVIEW, analysis=None, client=""):
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-1", "in-1", status, "cliente@example.com", analysis or ready_analysis())
        if client:
            db.execute("INSERT INTO quote_metadata (thread_id, client_name, updated_at) VALUES ('t-1', ?, 'x')",
                       (client,))
        db.commit()
        db.close()

    def approve(self, **kwargs):
        return case_actions.approve_review("t-1", **kwargs)

    def state(self):
        quote = quotes.get_quote("t-1")
        return quote["status"], quote["folder_id"], quote["analysis"], quote["client_name"]

    def test_existing_client_folder_is_reused_ignoring_case_accents_and_suffix(self):
        message = self.approve(client_name="minera sur")
        status, folder_id, analysis, _ = self.state()
        self.assertEqual(status, QuoteStatus.READY)
        self.assertRegex(self.drive.path(folder_id), r"^2026/Minera Sur S\.A\.S\./Cotizacion - Proyecto Norte - ")
        self.assertEqual(len(self.drive.created), 1)  # solo la carpeta de la solicitud
        self.assertNotIn("nuevo", message.lower())

    def test_unknown_client_is_not_created_without_explicit_confirmation(self):
        with self.assertRaisesRegex(ErrorDeAnka, "cliente nuevo"):
            self.approve(client_name="Empresa Nueva")
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.state()[0], QuoteStatus.READY_REVIEW)

    def test_confirmed_new_client_creates_client_and_request_folders(self):
        message = self.approve(client_name="Empresa Nueva", new_client=True)
        status, folder_id, _, client_name = self.state()
        self.assertEqual(status, QuoteStatus.READY)
        self.assertEqual([p.split("/", 2)[1] for p in self.drive.created], ["Empresa Nueva", "Empresa Nueva"])
        self.assertRegex(self.drive.path(folder_id), r"^2026/Empresa Nueva/")
        self.assertEqual(client_name, "Empresa Nueva")  # recordado para los próximos correos
        self.assertIn("cliente nuevo", message)

    def test_new_client_flag_does_not_duplicate_an_existing_client(self):
        self.approve(client_name="Minera Sur", new_client=True,
            allow_similar_project=False)
        self.assertEqual(len(self.drive.created), 1)

    def test_client_name_is_required_unless_a_person_already_set_it(self):
        with self.assertRaisesRegex(ErrorDeAnka, "carpeta del cliente"):
            self.approve()
        db = store.open_db(Path(self._tmp.name))
        db.execute("INSERT INTO quote_metadata (thread_id, client_name, updated_at) VALUES ('t-1', 'Minera Sur', 'x')")
        db.commit()
        db.close()
        self.approve()
        self.assertEqual(self.state()[0], QuoteStatus.READY)

    def test_ambiguous_client_folders_are_reported(self):
        self.drive = FakeDriveTree({"Acme": {}, "ACME SAS": {}})
        with self.assertRaisesRegex(ErrorDeAnka, "varias"):
            self.approve(client_name="acme corp")
        self.assertEqual(self.drive.created, [])

    def test_parent_folder_from_another_year_blocks_the_creation(self):
        self.drive = FakeDriveTree({"Minera Sur": {}}, root_name="2025")
        with self.assertRaisesRegex(ErrorDeAnka, "2025.*2026"):
            self.approve(client_name="Minera Sur")
        self.assertEqual(self.drive.created, [])

    def test_the_pending_drive_hint_is_cleared_once_the_folder_exists(self):
        self.seed(analysis=ready_analysis(drive={"pending": "falta confirmar"}))
        self.approve(client_name="Minera Sur")
        self.assertNotIn("drive", self.state()[2])

    def test_only_cases_waiting_for_approval_can_be_approved(self):
        self.seed(status=QuoteStatus.IGNORED)
        with self.assertRaisesRegex(ErrorDeAnka, "no pendiente"):
            self.approve(client_name="Minera Sur")

    def test_the_audit_line_never_contains_the_client_name(self):
        with self.assertLogs("anka.servicios.case_actions", "INFO") as logs:
            self.approve(client_name="Empresa Secreta", new_client=True)
        text = " ".join(logs.output)
        self.assertIn("t-1", text)
        self.assertNotIn("Secreta", text)


# Cubre: F1-07
class ApproveRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("anka.web.paginas.load_config", return_value=test_config()), patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            p.start()
            self.addCleanup(p.stop)

    def post(self, fields, approve_result="ok", side_effect=None):
        body = "&".join(f"{k}={v}" for k, v in {"csrf_token": seguridad.CSRF_TOKEN, **fields}.items()).encode()
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        sent = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(body))}
        handler.path = "/approve"
        handler.rfile = io.BytesIO(body)
        handler._send_html = lambda content, status=200: sent.update(status=status, page=content)
        handler._redirect = lambda location: sent.update(status=303, location=location)
        with patch("anka.web.acciones.approve_review", return_value=approve_result, side_effect=side_effect) as mock:
            handler.do_POST()
        return sent, mock

    def test_form_fields_reach_approve_review_and_the_user_returns_to_the_case(self):
        sent, mock = self.post({"thread_id": "t-1", "client_name": "Minera+Sur", "new_client": "1"})
        mock.assert_called_once_with("t-1", body_text=None, client_name="Minera Sur", new_client=True,
            allow_similar_project=False)
        self.assertEqual(sent["status"], 303)
        self.assertTrue(sent["location"].startswith("/case?thread_id=t-1&message="))

    def test_checkbox_off_means_no_new_client(self):
        _, mock = self.post({"thread_id": "t-1", "client_name": "Minera+Sur"})
        self.assertIs(mock.call_args.kwargs["new_client"], False)

    def test_guidance_errors_are_shown_on_the_case_page_not_as_a_500(self):
        sent, _ = self.post({"thread_id": "t-1", "client_name": "X"},
                            side_effect=ErrorDeAnka("Confirma que es un cliente nuevo"))
        self.assertEqual(sent["status"], 303)
        self.assertIn("cliente%20nuevo", sent["location"])
        self.assertIn("/case?thread_id=t-1", sent["location"])


# Cubre: F1-07
class CliTests(unittest.TestCase):
    def test_approve_command_accepts_client_name_and_new_client(self):
        import sys

        from anka.cli import app
        argv = ["app.py", "approve", "--thread-id", "t-1", "--client-name", "Minera Sur", "--new-client"]
        with patch.object(sys, "argv", argv), patch("anka.cli.app.approve_review") as mock, \
                patch("anka.cli.app.configure_logging"), patch("anka.cli.app.warn_unknown_config_keys"):
            app.main()
        mock.assert_called_once_with("t-1", None, client_name="Minera Sur", new_client=True)


# Cubre: F1-07
class MetadataTests(unittest.TestCase):
    def test_set_client_name_keeps_the_other_commercial_data(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            db = store.open_db(Path(d))
            store.save(db, "t-1", "m", QuoteStatus.REVIEW, "a@b.co", {})
            db.execute("INSERT INTO quote_metadata (thread_id, client_type, sector, client_name, notes, updated_at)"
                       " VALUES ('t-1', 'Empresa privada', 'Minería', '', 'nota', 'x')")
            db.commit()
            db.close()
            quotes.set_client_name("t-1", "  Minera Sur ")
            quote = quotes.get_quote("t-1")
            self.assertEqual((quote["client_name"], quote["client_type"], quote["sector"], quote["notes"]),
                             ("Minera Sur", "Empresa privada", "Minería", "nota"))

    def test_set_client_name_creates_metadata_when_there_is_none(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            db = store.open_db(Path(d))
            store.save(db, "t-1", "m", QuoteStatus.REVIEW, "a@b.co", {})
            db.close()
            quotes.set_client_name("t-1", "Minera Sur")
            self.assertEqual(quotes.get_quote("t-1")["client_name"], "Minera Sur")
            self.assertEqual(json.dumps(quotes.get_quote("t-1")["client_type"]), '"No clasificado"')


if __name__ == "__main__":
    unittest.main()
