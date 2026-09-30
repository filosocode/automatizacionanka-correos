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
import pipeline
import quotes
import settings
import store
from status import QuoteStatus
from test_app import FakeGmail, FakeRequest, complete_analysis, encoded_message, test_config

QUOTE_TEXT = "Solicito cotización de un levantamiento de 10 ha."


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


class RunFlowTests(unittest.TestCase):
    def run_flow(self, gmail, analysis=None, *, seed=None, **flags):
        """Ejecuta run() aislado; devuelve (fila, analyze_mock, paquetes, gmail, folder_mock)."""
        flags.setdefault("review_only", False)
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(settings, "DATA_DIR", Path(temp_dir)
        ), patch("pipeline.load_config", return_value=test_config()), patch(
            "pipeline.services", return_value=(gmail, object())
        ), patch("pipeline.analyze", return_value=analysis) as analyze_mock, patch(
            "pipeline.ensure_folder", return_value={"id": "folder-1"}
        ) as folder_mock:
            if seed:
                db = quotes.init_db()
                store.save(db, *seed)
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
        return rows, analyze_mock, packets, folder_mock

    @staticmethod
    def thread(text=QUOTE_TEXT, thread_id="t-1", parts=None, subject="Solicitud de cotización"):
        return {"id": thread_id, "messages": [
            encoded_message("client-1", "cliente@example.com", text, 1, parts=parts,
                            subject=subject)
        ]}

    def test_message_without_commercial_terms_is_ignored_without_analysis(self):
        rows, analyze_mock, _, _ = self.run_flow(FakeGmail(self.thread("Hola, ¿cómo estás?", subject="Saludo")))
        self.assertEqual(rows[0][1], QuoteStatus.IGNORED)
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

    def test_complete_scope_direct_mode_creates_drive_folder(self):
        rows, _, _, folder_mock = self.run_flow(FakeGmail(self.thread()), complete_analysis())
        self.assertEqual(rows[0][1], QuoteStatus.READY)
        self.assertEqual(rows[0][4], "folder-1")
        folder_mock.assert_called_once()

    def test_complete_scope_review_only_never_touches_drive(self):
        rows, _, packets, folder_mock = self.run_flow(
            FakeGmail(self.thread()), complete_analysis(), review_only=True
        )
        self.assertEqual(rows[0][1], QuoteStatus.READY_REVIEW)
        self.assertEqual(len(packets), 1)
        folder_mock.assert_not_called()

    def test_unreadable_attachment_without_commercial_terms_needs_review(self):
        attachment = base64.urlsafe_b64encode(b"binary").decode()
        parts = [{"filename": "plano.dwg", "mimeType": "application/acad",
                  "body": {"attachmentId": "att-1"}}]
        gmail = FakeGmail(self.thread("Hola, adjunto.", parts=parts, subject="Plano"),
                          {("client-1", "att-1"): attachment})
        rows, analyze_mock, _, _ = self.run_flow(gmail, review_only=True)
        self.assertEqual(rows[0][1], QuoteStatus.REVIEW)
        analyze_mock.assert_not_called()

    def test_already_processed_inbound_is_not_reanalyzed(self):
        seed = ("t-1", "client-1", QuoteStatus.READY, "cliente@example.com", {})
        rows, analyze_mock, _, folder_mock = self.run_flow(
            FakeGmail(self.thread()), complete_analysis(), seed=seed
        )
        self.assertEqual(rows[0][1], QuoteStatus.READY)
        analyze_mock.assert_not_called()
        folder_mock.assert_not_called()

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

    def test_prefilter_lets_candidates_through(self):
        self.assertIsNone(pipeline.decide_prefilter("t-1", {}, [], True))
        ignored = pipeline.decide_prefilter("t-1", {}, [], False)
        self.assertEqual(ignored.status, QuoteStatus.IGNORED)


if __name__ == "__main__":
    unittest.main()
