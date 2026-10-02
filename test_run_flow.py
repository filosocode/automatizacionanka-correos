"""Caracterización del bucle de `pipeline.run`: qué estado queda para cada tipo de hilo."""

import _isolate  # noqa: F401  (debe ir primero)

import base64
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import logging_setup
from fake_drive import FakeDriveTree
import pipeline
import quotes
import settings
import store
from status import QuoteStatus
from test_app import FakeGmail, FakeRequest, complete_analysis, encoded_message, test_config

QUOTE_TEXT = "Solicito cotización de un levantamiento de 10 ha."
INTERNAL_DATE = 1788271200000  # 2026-09-01: el año de la carpeta padre de los tests


class UnreadableThreadGmail(FakeGmail):
    def users(self):
        gmail = self

        class Users(super().users().__class__):
            def threads(self):
                class Threads:
                    def get(self, **kwargs):
                        class Failing(FakeRequest):
                            def execute(self):
                                raise RuntimeError("404 hilo eliminado")
                        return Failing(None)
                return Threads()

        return Users(gmail)


# Cubre: F1-01, F1-02, F1-04, F1-05
class RunFlowTests(unittest.TestCase):
    def run_flow(self, gmail, analysis=None, *, seed=None, drive=None, metadata=None, **flags):
        """Ejecuta run() aislado; devuelve (filas, analyze_mock, paquetes, drive).

        `drive` es un FakeDriveTree (por defecto uno con el cliente "Minera Sur" bajo "2026");
        `metadata` = {thread_id: nombre_de_cliente} que una persona ya confirmó.
        """
        flags.setdefault("review_only", False)
        drive = drive or FakeDriveTree({"Minera Sur": {}})
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(settings, "DATA_DIR", Path(temp_dir)
        ), patch("pipeline.load_config", return_value=test_config()), patch(
            "pipeline.services", return_value=(gmail, drive)
        ), patch("pipeline.analyze", return_value=analysis) as analyze_mock:
            if seed:
                db = quotes.init_db()
                store.save(db, *seed)
                db.close()
            for thread_id, client in (metadata or {}).items():
                db = quotes.init_db()
                db.execute(
                    "INSERT INTO quote_metadata (thread_id, client_name, updated_at) VALUES (?, ?, 'x')",
                    (thread_id, client))
                db.commit()
                db.close()
            with redirect_stdout(io.StringIO()):
                pipeline.run(True, **flags)
            db = sqlite3.connect(Path(temp_dir) / "quotes.sqlite3")
            try:
                rows = db.execute(
                    "SELECT last_inbound_id, status, recipient, analysis_json, folder_id,"
                    " sent_message_id FROM quotes"
                ).fetchall()
            finally:
                db.close()
            queue = Path(temp_dir) / "review_queue"
            packets = [p.read_text(encoding="utf-8") for p in queue.glob("*.txt")] \
                if queue.exists() else []
        return rows, analyze_mock, packets, drive

    @staticmethod
    def thread(text=QUOTE_TEXT, thread_id="t-1", parts=None, subject="Solicitud de cotización"):
        return {"id": thread_id, "messages": [
            encoded_message("client-1", "cliente@example.com", text, INTERNAL_DATE, parts=parts,
                            subject=subject)
        ]}

    def test_implicit_request_without_keywords_reaches_the_model(self):
        # Antes un filtro de palabras clave la descartaba sin que el modelo la viera.
        gmail = FakeGmail(self.thread("Buenas, ¿cuánto vale medir un lote de 5 ha en Cota?",
                                      subject="Consulta"))
        rows, analyze_mock, _, _ = self.run_flow(gmail, complete_analysis(), review_only=True)
        analyze_mock.assert_called_once()
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)

    def automated_thread(self, subject="Ofertas", text="Descuentos de oficina"):
        return {"id": "t-1", "messages": [
            encoded_message("n-1", "Tienda <noreply@tienda.com>", text, 1, subject=subject)]}

    def test_automated_newsletter_is_ignored_with_a_reason_without_analysis(self):
        rows, analyze_mock, _, _ = self.run_flow(FakeGmail(self.automated_thread()))
        analyze_mock.assert_not_called()
        self.assertEqual(rows[0][1], QuoteStatus.IGNORED)
        saved = json.loads(rows[0][3])
        self.assertIn("no-reply", saved["ignored_reason"])
        self.assertEqual(saved["email"]["subject"], "Ofertas")
        self.assertIn("noreply@tienda.com", saved["email"]["from"])

    def test_automated_message_that_mentions_a_quote_is_still_analyzed(self):
        gmail = FakeGmail(self.automated_thread("Solicitud de cotización #88", "Se registró."))
        rows, analyze_mock, _, _ = self.run_flow(gmail, complete_analysis(), review_only=True)
        analyze_mock.assert_called_once()
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)

    def test_model_verdict_of_no_quote_is_ignored_and_records_why_and_the_email(self):
        analysis = complete_analysis() | {"is_quote_request": False}
        rows, _, _, _ = self.run_flow(FakeGmail(self.thread("Gracias por todo.", subject="Saludo")),
                                      analysis)
        saved = json.loads(rows[0][3])
        self.assertEqual(rows[0][1], QuoteStatus.IGNORED)
        self.assertIn("modelo", saved["ignored_reason"])
        self.assertEqual(saved["email"]["subject"], "Saludo")

    def test_rescued_thread_is_analyzed_even_if_it_looks_automated(self):
        seed = ("t-1", "", QuoteStatus.IGNORED, "noreply@tienda.com",
                {"ignored_reason": "remitente automático (no-reply)", "force_analysis": True})
        rows, analyze_mock, _, _ = self.run_flow(
            FakeGmail(self.automated_thread()), complete_analysis(), seed=seed, review_only=True)
        analyze_mock.assert_called_once()
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)
        self.assertNotIn("force_analysis", json.loads(rows[0][3]))

    def test_discarded_thread_without_rescue_is_not_analyzed_again(self):
        seed = ("t-1", "n-1", QuoteStatus.IGNORED, "noreply@tienda.com", {"ignored_reason": "x"})
        _, analyze_mock, _, _ = self.run_flow(
            FakeGmail(self.automated_thread()), complete_analysis(), seed=seed)
        analyze_mock.assert_not_called()

    def test_non_quote_analysis_is_ignored(self):
        analysis = complete_analysis()
        analysis["is_quote_request"] = False
        rows, _, _, _ = self.run_flow(FakeGmail(self.thread()), analysis)
        self.assertEqual(rows[0][1], QuoteStatus.IGNORED)

    def test_no_activities_goes_to_manual_review(self):
        analysis = complete_analysis()
        analysis["activities"] = []
        rows, _, _, _ = self.run_flow(FakeGmail(self.thread()), analysis)
        self.assertEqual(rows[0][1], QuoteStatus.REVIEW)

    def test_missing_data_review_only_saves_packet_without_sending(self):
        gmail = FakeGmail(self.thread())
        rows, _, packets, _ = self.run_flow(
            gmail, complete_analysis(location=""), review_only=True
        )
        self.assertEqual(rows[0][1], QuoteStatus.REVIEW_PENDING)
        self.assertEqual(len(packets), 1)
        self.assertEqual(gmail.sent_messages, [])

    def test_missing_data_direct_mode_sends_question_and_waits(self):
        gmail = FakeGmail(self.thread())
        rows, _, _, _ = self.run_flow(gmail, complete_analysis(location=""))
        self.assertEqual(rows[0][1], QuoteStatus.WAITING)
        self.assertEqual(rows[0][5], "sent-1")
        self.assertEqual(len(gmail.sent_messages), 1)

    def test_complete_scope_review_only_never_touches_drive(self):
        rows, _, packets, drive = self.run_flow(
            FakeGmail(self.thread()), complete_analysis(), review_only=True
        )
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)
        self.assertEqual(len(packets), 1)
        self.assertEqual(drive.created, [])

    def test_unreadable_attachment_never_lets_a_no_quote_verdict_discard_the_thread(self):
        # La solicitud puede estar dentro del adjunto que no se pudo leer.
        attachment = base64.urlsafe_b64encode(b"binary").decode()
        parts = [{"filename": "plano.dwg", "mimeType": "application/acad",
                  "body": {"attachmentId": "att-1"}}]
        gmail = FakeGmail(self.thread("Hola, adjunto.", parts=parts, subject="Plano"),
                          {("client-1", "att-1"): attachment})
        analysis = complete_analysis() | {"is_quote_request": False}
        rows, analyze_mock, _, _ = self.run_flow(gmail, analysis, review_only=True)
        analyze_mock.assert_called_once()
        self.assertEqual(rows[0][1], QuoteStatus.REVIEW)

    def test_already_processed_inbound_is_not_reanalyzed(self):
        seed = ("t-1", "client-1", QuoteStatus.READY, "cliente@example.com", {})
        rows, analyze_mock, _, drive = self.run_flow(
            FakeGmail(self.thread()), complete_analysis(), seed=seed
        )
        self.assertEqual(rows[0][1], QuoteStatus.READY)
        analyze_mock.assert_not_called()
        self.assertEqual(drive.created, [])

    def test_interrupted_send_is_never_retried(self):
        seed = ("t-1", "client-1", QuoteStatus.SENDING, "cliente@example.com", {})
        gmail = FakeGmail(self.thread())
        rows, analyze_mock, _, _ = self.run_flow(
            gmail, complete_analysis(location=""), seed=seed
        )
        self.assertEqual(rows[0][1], QuoteStatus.SENDING)
        self.assertEqual(gmail.sent_messages, [])
        analyze_mock.assert_not_called()

    def test_thread_with_only_own_messages_is_skipped(self):
        thread = {"id": "t-1", "messages": [
            encoded_message("own-1", "Anka <alias@anka.com.co>", QUOTE_TEXT, 1)
        ]}
        rows, analyze_mock, _, _ = self.run_flow(FakeGmail(thread))
        self.assertEqual(rows, [])
        analyze_mock.assert_not_called()

    def test_waiting_thread_missing_in_gmail_is_marked_missing(self):
        seed = ("t-1", "client-1", QuoteStatus.WAITING, "cliente@example.com", {})
        rows, _, _, _ = self.run_flow(UnreadableThreadGmail(self.thread()), seed=seed)
        self.assertEqual(rows[0][1], QuoteStatus.MISSING)

    def test_saved_analysis_keeps_routing_and_received_at(self):
        rows, _, _, _ = self.run_flow(
            FakeGmail(self.thread()), complete_analysis(), review_only=True
        )
        saved = json.loads(rows[0][3])
        self.assertEqual(saved["routing"]["selected_recipient"], "cliente@example.com")
        self.assertIn("received_at", saved)


# Cubre: F1-07, R4, R7
class DriveStructureFlowTests(unittest.TestCase):
    """Carpeta del proyecto: se crea sola solo cuando el cliente es seguro; si no, espera a una persona."""

    def run_complete(self, *, thread=None, drive=None, metadata=None, seed=None, **flags):
        thread = thread or RunFlowTests.thread()
        return RunFlowTests().run_flow(
            FakeGmail(thread), complete_analysis(), drive=drive, metadata=metadata, seed=seed, **flags)

    def assert_held(self, rows, drive, why):
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)
        self.assertIsNone(rows[0][4])  # sin folder_id
        self.assertEqual(drive.created, [])
        pending = json.loads(rows[0][3])["drive"]["pending"]
        self.assertIn(why, pending)

    def test_known_client_gets_the_request_folder_under_its_existing_client_folder(self):
        seed = ("t-1", "old", QuoteStatus.WAITING, "cliente@example.com", {})
        rows, _, _, drive = self.run_complete(metadata={"t-1": "MINERA SUR S.A.S."}, seed=seed)
        self.assertEqual(rows[0][1], QuoteStatus.READY)
        self.assertEqual(len(drive.created), 1)
        self.assertRegex(drive.created[0], r"^2026/Minera Sur/Cotizacion - Proyecto Norte - ")
        self.assertIsNotNone(rows[0][4])

    def test_without_a_confirmed_client_the_folder_waits_for_a_person(self):
        rows, _, _, drive = self.run_complete()
        self.assert_held(rows, drive, "cliente")

    def test_a_client_confirmed_in_a_previous_thread_of_the_same_sender_is_reused(self):
        db_seed = ("t-prev", "p-1", QuoteStatus.READY, "cliente@example.com", {})
        rows, _, _, drive = RunFlowTests().run_flow(
            FakeGmail(RunFlowTests.thread(thread_id="t-new")), complete_analysis(),
            seed=db_seed, metadata={"t-prev": "Minera Sur"})
        new = [r for r in rows if r[0] != "p-1"][0]
        self.assertEqual(new[1], QuoteStatus.READY)
        self.assertRegex(drive.created[0], r"^2026/Minera Sur/")

    def test_a_client_without_a_folder_is_never_created_automatically(self):
        seed = ("t-1", "old", QuoteStatus.WAITING, "cliente@example.com", {})
        rows, _, _, drive = self.run_complete(metadata={"t-1": "Empresa Nueva"}, seed=seed)
        self.assert_held(rows, drive, "Empresa Nueva")

    def test_ambiguous_client_folders_wait_for_a_person(self):
        seed = ("t-1", "old", QuoteStatus.WAITING, "cliente@example.com", {})
        drive = FakeDriveTree({"Acme": {}, "ACME SAS": {}})
        rows, _, _, drive = self.run_complete(metadata={"t-1": "Acme Corp"}, seed=seed, drive=drive)
        self.assert_held(rows, drive, "varias")

    def test_parent_folder_of_another_year_waits_for_a_person(self):
        seed = ("t-1", "old", QuoteStatus.WAITING, "cliente@example.com", {})
        drive = FakeDriveTree({"Minera Sur": {}}, root_name="2025")
        rows, _, _, drive = self.run_complete(metadata={"t-1": "Minera Sur"}, seed=seed, drive=drive)
        self.assert_held(rows, drive, "2025")

    def test_forwarded_request_looks_up_the_original_sender_not_the_internal_reviewer(self):
        forwarded = {"id": "t-1", "messages": [encoded_message(
            "fwd-1", "Diego <diego@anka.com.co>",
            "---------- Mensaje reenviado ---------\n"
            "De: Cliente Real <real@minera.co>\n"
            "Asunto: x\n\nSolicitamos cotización de levantamiento.",
            INTERNAL_DATE, subject="Fwd: Solicitud de cotización")]}
        db_seed = ("t-prev", "p-1", QuoteStatus.READY, "diego@anka.com.co",
                   {"routing": {"original_sender_email": "real@minera.co"}})
        rows, _, _, drive = RunFlowTests().run_flow(
            FakeGmail(forwarded), complete_analysis(), seed=db_seed, metadata={"t-prev": "Minera Sur"})
        new = [r for r in rows if r[0] != "p-1"][0]
        self.assertEqual(new[1], QuoteStatus.READY)
        self.assertRegex(drive.created[0], r"^2026/Minera Sur/")

    def test_a_held_case_says_why_without_putting_the_client_name_in_the_file_log(self):
        seed = ("t-1", "old", QuoteStatus.WAITING, "cliente@example.com", {})
        with self.assertLogs("anka.pipeline", "WARNING") as logs:
            self.run_complete(metadata={"t-1": "Empresa Secreta"}, seed=seed)
        text = " ".join(logs.output)
        self.assertIn("pendiente de confirmación", text)

    def test_a_held_case_is_not_retried_on_every_review(self):
        rows, analyze_mock, _, _ = self.run_complete()
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)
        seed = ("t-1", "client-1", QuoteStatus.READY_REVIEW, "cliente@example.com", {})
        _, analyze_mock, _, drive = self.run_complete(seed=seed)
        analyze_mock.assert_not_called()
        self.assertEqual(drive.created, [])


# Cubre: T-08
class LogPrivacyTests(unittest.TestCase):
    """El archivo de registro guarda resultados por hilo, nunca contenido de clientes."""

    CLIENT_EMAIL = "cliente.secreto@empresa-privada.co"
    SUMMARY = "Levantamiento topográfico confidencial del parque Solaris"

    def run_logged(self, gmail, analysis, **flags):
        with tempfile.TemporaryDirectory() as log_dir:
            console = io.StringIO()
            logging_setup.configure_logging(Path(log_dir), stream=console)
            self.addCleanup(logging_setup.reset_logging)
            RunFlowTests().run_flow(gmail, analysis, **flags)
            logging_setup.reset_logging()
            file_text = (Path(log_dir) / "anka.log").read_text(encoding="utf-8")
        return console.getvalue(), file_text

    def thread(self):
        return {"id": "t-log", "messages": [encoded_message(
            "client-1", self.CLIENT_EMAIL, "Solicito cotización de un levantamiento.", 1)]}

    def analysis(self, **extra):
        return complete_analysis(location="") | {"summary": self.SUMMARY} | extra

    def test_file_has_the_outcome_but_no_client_content(self):
        console, file_text = self.run_logged(
            FakeGmail(self.thread()), self.analysis(), gmail_drafts=True, review_only=False,
        )
        self.assertIn("t-log: solicitud de cotización analizada", file_text)
        self.assertIn("t-log: borrador creado en Gmail: draft-1", file_text)
        for secret in (self.CLIENT_EMAIL, "empresa-privada", self.SUMMARY, "Levantamiento topográfico",
                       "Borrador para", "ubicación del proyecto"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, file_text)

    def test_console_keeps_the_full_detail_the_operator_used_to_see(self):
        console, _ = self.run_logged(
            FakeGmail(self.thread()), self.analysis(), gmail_drafts=True, review_only=False,
        )
        self.assertIn(self.SUMMARY, console)
        self.assertIn("Borrador para", console)
        self.assertIn(self.CLIENT_EMAIL, console)

    def test_manual_review_and_direct_send_leave_an_audit_line(self):
        _, review_file = self.run_logged(
            FakeGmail(self.thread()), self.analysis(activities=[]), review_only=True,
        )
        self.assertIn("t-log: sin actividades identificables; requiere revisión manual", review_file)
        _, send_file = self.run_logged(FakeGmail(self.thread()), self.analysis())
        self.assertIn("t-log: preguntas enviadas al cliente; se espera respuesta", send_file)
        self.assertNotIn(self.CLIENT_EMAIL, send_file)

    def test_warning_level_marks_the_cases_that_need_a_human(self):
        with self.assertLogs("anka.pipeline", "WARNING") as logs:
            RunFlowTests().run_flow(FakeGmail(self.thread()), self.analysis(activities=[]),
                                    review_only=True)
        self.assertTrue(any("requiere revisión manual" in line for line in logs.output))


# Cubre: F1-04, T-01, R2
class DecideAnalysisTests(unittest.TestCase):
    """Reglas de decisión puras: sin Gmail, SQLite ni Drive."""

    def decide(self, analysis, *, blocked=(), review_only=False, gmail_drafts=False):
        return pipeline.decide_analysis(
            "t-1", analysis, list(blocked), "cliente@example.com", False, test_config(),
            review_only=review_only, gmail_drafts=gmail_drafts,
        )

    def test_mode_flags_pick_the_action_for_missing_data(self):
        analysis = complete_analysis(location="")
        cases = [
            ({}, QuoteStatus.WAITING, pipeline.Action.SEND),
            ({"review_only": True}, QuoteStatus.REVIEW_PENDING, pipeline.Action.QUEUE),
            ({"gmail_drafts": True}, QuoteStatus.DRAFT_CREATED, pipeline.Action.GMAIL_DRAFT),
        ]
        for flags, status, action in cases:
            with self.subTest(flags=flags):
                decision = self.decide(analysis, **flags)
                self.assertEqual((decision.status, decision.action), (status, action))
                self.assertTrue(decision.body)

    def test_blocked_attachment_wins_over_everything_else(self):
        decision = self.decide(complete_analysis(), blocked=[{"filename": "plano.dwg"}])
        self.assertEqual(decision.status, QuoteStatus.REVIEW)
        self.assertEqual(decision.action, pipeline.Action.NONE)
        self.assertTrue(decision.packet)

    def test_complete_scope_names_the_folder(self):
        decision = self.decide(complete_analysis())
        self.assertEqual(decision.status, QuoteStatus.READY)
        self.assertEqual(decision.action, pipeline.Action.FOLDER)
        self.assertIn("Proyecto Norte", decision.folder_name)

    def test_prefilter_only_discards_when_there_is_a_noise_reason(self):
        self.assertIsNone(pipeline.decide_prefilter("t-1", None, {}))
        ignored = pipeline.decide_prefilter("t-1", "lista de correo", {"subject": "S"})
        self.assertEqual(ignored.status, QuoteStatus.IGNORED)
        self.assertEqual(ignored.analysis["ignored_reason"], "lista de correo")
        self.assertEqual(ignored.analysis["email"], {"subject": "S"})
        self.assertFalse(any(note.sensitive for note in ignored.logs))
        private = pipeline.decide_prefilter("t-1", "lista", {"subject": "ASUNTO SECRETO"})
        self.assertNotIn("ASUNTO SECRETO", " ".join(note.text for note in private.logs))


if __name__ == "__main__":
    unittest.main()
