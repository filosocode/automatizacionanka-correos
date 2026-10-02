"""Carpeta del proyecto automática al enviar la respuesta: cliente nuevo/recurrente y duplicados."""

import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import case_actions
import drive_layout
import quotes
import settings
import store
from fake_drive import FakeDriveTree
from status import QuoteStatus
from test_app import FakeGmail, complete_analysis, encoded_message, test_config

REQUEST = "Cotizacion - Parque Solar Llanos 8 - abc12345"


# Cubre: F1-07, R9
class DriveLayoutAutoTests(unittest.TestCase):
    def test_new_client_without_similar_folders_is_created_automatically(self):
        drive = FakeDriveTree({"Minera Sur": {}})
        result = drive_layout.create_project_folder(
            drive, drive.root_id, "Solaris Energy", REQUEST, auto_create_client=True)
        self.assertTrue(result.client_created)
        self.assertEqual(drive.created, ["2026/Solaris Energy", f"2026/Solaris Energy/{REQUEST}"])

    def test_without_auto_a_new_client_still_needs_confirmation(self):
        drive = FakeDriveTree({"Minera Sur": {}})
        with self.assertRaises(drive_layout.ClientNotFound):
            drive_layout.create_project_folder(drive, drive.root_id, "Solaris Energy", REQUEST)
        self.assertEqual(drive.created, [])

    def test_similar_client_folder_blocks_the_automatic_creation(self):
        drive = FakeDriveTree({"Solaris Energy Colombia": {}})
        with self.assertRaises(drive_layout.SimilarClient) as caught:
            drive_layout.create_project_folder(
                drive, drive.root_id, "Solaris Energy", REQUEST, auto_create_client=True)
        self.assertIn("Solaris Energy Colombia", str(caught.exception))
        self.assertEqual(drive.created, [])

    def test_a_person_confirming_a_new_client_overrides_the_similar_check(self):
        drive = FakeDriveTree({"Solaris Energy Colombia": {}})
        result = drive_layout.create_project_folder(
            drive, drive.root_id, "Solaris Energy", REQUEST, create_client=True)
        self.assertTrue(result.client_created)

    def test_recurring_client_gets_a_new_request_folder_next_to_the_others(self):
        drive = FakeDriveTree({"Minera Sur": {"Cotizacion - Batimetría Embalse - 99999999": {}}})
        result = drive_layout.create_project_folder(
            drive, drive.root_id, "minera sur", REQUEST, auto_create_client=True)
        self.assertFalse(result.client_created)
        self.assertEqual(drive.created, [f"2026/Minera Sur/{REQUEST}"])

    def test_same_request_folder_is_reused_not_duplicated(self):
        drive = FakeDriveTree({"Minera Sur": {REQUEST: {}}})
        drive_layout.create_project_folder(drive, drive.root_id, "Minera Sur", REQUEST, auto_create_client=True)
        self.assertEqual(drive.created, [])

    def test_a_similar_project_name_is_not_duplicated(self):
        drive = FakeDriveTree({"Minera Sur": {"Cotizacion - Parque Solar Llanos 8 Fase 1 - 11111111": {}}})
        with self.assertRaises(drive_layout.SimilarProject) as caught:
            drive_layout.create_project_folder(
                drive, drive.root_id, "Minera Sur", REQUEST, auto_create_client=True)
        self.assertIn("nombre parecido", str(caught.exception))
        self.assertEqual(drive.created, [])

    def test_the_same_attachment_in_another_folder_marks_it_as_the_same_project(self):
        drive = FakeDriveTree({"Minera Sur": {"Cotizacion - Estudio vial - 22222222": {}}})
        drive.add_file(drive.folder_id("Minera Sur/Cotizacion - Estudio vial - 22222222"), "ET Topografia Llanos 8.pdf")
        with self.assertRaises(drive_layout.SimilarProject) as caught:
            drive_layout.create_project_folder(
                drive, drive.root_id, "Minera Sur", REQUEST, auto_create_client=True,
                attachment_names=["et topografia llanos 8.PDF"])
        self.assertIn("mismo archivo", str(caught.exception))

    def test_a_person_can_force_a_distinct_project(self):
        drive = FakeDriveTree({"Minera Sur": {"Cotizacion - Parque Solar Llanos 8 Fase 1 - 11111111": {}}})
        drive_layout.create_project_folder(
            drive, drive.root_id, "Minera Sur", REQUEST, auto_create_client=True, allow_similar_project=True)
        self.assertEqual(drive.created, [f"2026/Minera Sur/{REQUEST}"])

    def test_unrelated_projects_do_not_block(self):
        drive = FakeDriveTree({"Minera Sur": {"Cotizacion - Batimetría Embalse La Esperanza - 33333333": {}}})
        drive_layout.create_project_folder(drive, drive.root_id, "Minera Sur", REQUEST, auto_create_client=True)
        self.assertEqual(len(drive.created), 1)


# Cubre: F1-07, R9
class SendCreatesTheFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.drive = FakeDriveTree({"Minera Sur": {}})
        self.gmail = FakeGmail({"id": "t-1", "messages": [
            encoded_message("m-1", "cliente@example.com", "Solicito cotización.", 1, subject="Solicitud")]})
        for p in (
            patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
            patch("case_actions.load_config", return_value=test_config()),
            patch("case_actions.services", side_effect=lambda: (self.gmail, self.drive)),
        ):
            p.start()
            self.addCleanup(p.stop)

    def seed(self, client="", status=QuoteStatus.READY_REVIEW):
        analysis = complete_analysis() | {"received_at": "2026-09-01T09:00:00-05:00"}
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-1", "m-1", status, "cliente@example.com", analysis)
        if client:
            db.execute("INSERT INTO quote_metadata (thread_id, client_name, updated_at) VALUES ('t-1', ?, 'x')",
                       (client,))
        db.commit()
        db.close()

    def quote(self):
        return quotes.get_quote("t-1")

    def test_sending_with_a_known_client_creates_the_folder_and_closes_the_case(self):
        self.seed(client="Minera Sur")
        message = case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
        self.assertIn("Correo enviado", message)
        self.assertIn("Carpeta en Drive", message)
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.READY)
        self.assertTrue(quote["folder_id"])
        self.assertEqual(len(self.drive.created), 1)

    def test_a_brand_new_client_name_creates_its_folder_too(self):
        self.seed(client="Solaris Energy")
        case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertEqual(self.quote()["status"], QuoteStatus.READY)
        self.assertEqual(self.drive.created[0], "2026/Solaris Energy")

    def test_a_recurring_sender_is_recognised_from_another_case(self):
        self.seed()
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-old", "m-0", QuoteStatus.READY, "cliente@example.com", complete_analysis())
        db.execute("INSERT INTO quote_metadata (thread_id, client_name, updated_at) VALUES ('t-old', 'Minera Sur', 'x')")
        db.commit()
        db.close()
        case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertEqual(self.quote()["status"], QuoteStatus.READY)
        self.assertTrue(self.drive.created[0].startswith("2026/Minera Sur/"))

    def test_without_a_client_the_email_still_goes_out_and_the_reason_is_shown(self):
        self.seed()
        message = case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertIn("Correo enviado", message)
        self.assertIn("No creé la carpeta", message)
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.READY_REVIEW)
        self.assertTrue(quote["sent_at"])
        self.assertIn("carpeta del cliente", quote["analysis"]["drive"]["pending"])
        self.assertEqual(self.drive.created, [])

    def test_an_email_address_is_never_used_as_the_client_folder_name(self):
        self.seed(client="cliente@example.com")
        message = case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertIn("No creé la carpeta", message)
        self.assertEqual(self.drive.created, [])
        self.assertEqual(self.quote()["status"], QuoteStatus.READY_REVIEW)

    def test_a_similar_client_leaves_the_case_open_with_the_reason(self):
        self.drive = FakeDriveTree({"Solaris Energy Colombia": {}})
        self.seed(client="Solaris Energy")
        message = case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertIn("parecida", message)
        self.assertEqual(self.quote()["status"], QuoteStatus.READY_REVIEW)
        self.assertEqual(self.drive.created, [])

    def test_a_drive_failure_does_not_lose_the_sent_email(self):
        self.seed(client="Minera Sur")
        with patch("case_actions.create_project_folder", side_effect=RuntimeError("sin red")):
            with self.assertLogs("anka.case_actions", "ERROR"):
                message = case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertIn("Correo enviado", message)
        self.assertIn("No creé la carpeta", message)
        self.assertEqual(self.quote()["status"], QuoteStatus.READY_REVIEW)

    def test_only_cases_with_enough_information_trigger_the_folder(self):
        self.seed(status=QuoteStatus.REVIEW_PENDING, client="Minera Sur")
        message = case_actions.send_dashboard_reply("t-1", "Hola.")
        self.assertNotIn("carpeta", message.lower())
        self.assertEqual(self.drive.created, [])


if __name__ == "__main__":
    unittest.main()
