"""Decisión humana sobre un caso en revisión técnica: información suficiente o preguntar al cliente."""

import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quotes
import reception
import settings
import store
import web
from status import QuoteStatus
from test_app import complete_analysis, test_config


def technical_analysis() -> dict:
    analysis = complete_analysis()
    analysis["attachments"] = [{"filename": "plano.dwg", "status": "bloqueado", "detail": "plano CAD"}]
    return analysis


class ReviewDecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("web.load_config", return_value=test_config())):
            p.start()
            self.addCleanup(p.stop)
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-tech", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", technical_analysis())
        store.save(db, "t-ok", "m-2", QuoteStatus.REVIEW_PENDING, "otro@empresa.co", complete_analysis())
        db.close()

    def quote(self, thread_id="t-tech"):
        return quotes.get_quote(thread_id)

    def test_starts_as_technical_review(self):
        self.assertEqual(self.quote()["reception_assessment"]["status"], "technical_review")
        self.assertIn('action="/review-decision"', web._render_case("t-tech"))

    def test_sufficient_moves_to_ready_review_and_unlocks_the_drive_step(self):
        message = quotes.resolve_technical_review("t-tech", "sufficient")
        self.assertIn("carpeta", message)
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.READY_REVIEW)
        self.assertEqual(quote["analysis"]["manual_review"]["decision"], "sufficient")
        self.assertNotEqual(quote["reception_assessment"]["status"], "technical_review")
        page = web._render_case("t-tech")
        self.assertIn("Carpeta del proyecto en Drive", page)
        self.assertIn('action="/approve"', page)
        self.assertIn("Una persona revisó este caso", page)
        self.assertNotIn('action="/review-decision"', page)

    def test_ask_client_needs_questions_and_unlocks_the_reply_with_them(self):
        with self.assertRaisesRegex(ValueError, "al menos una pregunta"):
            quotes.resolve_technical_review("t-tech", "ask_client", "  \n ")
        self.assertEqual(self.quote()["status"], QuoteStatus.REVIEW)
        quotes.resolve_technical_review(
            "t-tech", "ask_client", "¿Qué validación altimétrica esperan?\n\n¿Hay KMZ del corredor?")
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.REVIEW_PENDING)
        reply = reception.suggested_reply(quote["analysis"])
        self.assertIn("1. ¿Qué validación altimétrica esperan?", reply)
        self.assertIn("2. ¿Hay KMZ del corredor?", reply)
        page = web._render_case("t-tech")
        self.assertIn('formaction="/send"', page)
        self.assertNotIn("Respuesta en pausa", page)

    def test_only_cases_in_technical_review_and_valid_decisions(self):
        with self.assertRaisesRegex(ValueError, "revisión técnica"):
            quotes.resolve_technical_review("t-ok", "sufficient")
        with self.assertRaisesRegex(ValueError, "no válida"):
            quotes.resolve_technical_review("t-tech", "otra")
        with self.assertRaisesRegex(ValueError, "no existe"):
            quotes.resolve_technical_review("no-existe", "sufficient")

    def test_a_new_analysis_discards_the_human_decision(self):
        quotes.resolve_technical_review("t-tech", "sufficient")
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-tech", "m-9", QuoteStatus.REVIEW, "cliente@empresa.co", technical_analysis())
        db.close()
        quote = self.quote()
        self.assertNotIn("manual_review", quote["analysis"])
        self.assertEqual(quote["reception_assessment"]["status"], "technical_review")

    def test_sending_a_reply_does_not_close_a_case_that_is_ready_for_its_drive_folder(self):
        from test_app import FakeGmail, encoded_message
        import case_actions
        quotes.resolve_technical_review("t-tech", "sufficient")
        gmail = FakeGmail({"id": "t-tech", "messages": [
            encoded_message("m-1", "cliente@empresa.co", "Solicito cotización.", 1, subject="Solicitud")]})
        with patch("case_actions.load_config", return_value=test_config()), patch(
            "case_actions.services", return_value=(gmail, object())
        ):
            case_actions.send_dashboard_reply("t-tech", "Hola, recibimos su solicitud.")
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.READY_REVIEW)
        self.assertTrue(quote["sent_at"])
        page = web._render_case("t-tech")
        self.assertIn('action="/approve"', page)
        self.assertIn("Ya se envió una respuesta", page)

    def request(self, path, body):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        sent = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(body))}
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler._send_html = lambda content, status=200: sent.update(status=status, page=content)
        handler._redirect = lambda location: sent.update(status=303, location=location)
        handler.do_POST()
        return sent

    def test_post_requires_csrf_and_redirects_back_to_the_case(self):
        with self.assertLogs("anka.web", "WARNING"):
            denied = self.request("/review-decision", b"thread_id=t-tech&decision=sufficient&csrf_token=malo")
        self.assertEqual(denied["status"], 403)
        self.assertEqual(self.quote()["status"], QuoteStatus.REVIEW)
        ok = self.request(
            "/review-decision", f"thread_id=t-tech&decision=sufficient&csrf_token={web.CSRF_TOKEN}".encode())
        self.assertEqual(ok["status"], 303)
        self.assertTrue(ok["location"].startswith("/case?thread_id=t-tech&message="))
        self.assertEqual(self.quote()["status"], QuoteStatus.READY_REVIEW)

    def test_post_with_missing_questions_shows_the_guidance_not_an_error_page(self):
        sent = self.request(
            "/review-decision", f"thread_id=t-tech&decision=ask_client&csrf_token={web.CSRF_TOKEN}".encode())
        self.assertEqual(sent["status"], 303)
        self.assertIn("pregunta", sent["location"])


if __name__ == "__main__":
    unittest.main()
