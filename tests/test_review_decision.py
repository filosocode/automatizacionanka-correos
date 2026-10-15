"""Decisión humana sobre un caso en revisión técnica: información suficiente o preguntar al cliente."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.servicios import quotes
from anka.dominio import reception
from anka.infra import settings
from anka.infra import store
from anka.web import server as web
from anka.web import seguridad
from anka.web import pantalla_caso
from anka.dominio.status import QuoteStatus
from tests.test_app import complete_analysis, test_config


def technical_analysis() -> dict:
    analysis = complete_analysis()
    analysis["attachments"] = [{"filename": "plano.dwg", "status": "bloqueado", "detail": "plano CAD"}]
    return analysis


# Cubre: T-01, F1-05
class ReviewDecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("anka.web.paginas.load_config", return_value=test_config()), patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
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
        self.assertIn('action="/review-decision"', pantalla_caso.render_case("t-tech"))

    def test_sufficient_moves_to_ready_review_and_unlocks_the_drive_step(self):
        message = quotes.resolve_technical_review("t-tech", "sufficient")
        self.assertIn("carpeta", message)
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.READY_REVIEW)
        self.assertEqual(quote["analysis"]["manual_review"]["decision"], "sufficient")
        self.assertNotEqual(quote["reception_assessment"]["status"], "technical_review")
        page = pantalla_caso.render_case("t-tech")
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
        page = pantalla_caso.render_case("t-tech")
        self.assertIn('formaction="/send"', page)
        self.assertNotIn("Respuesta en pausa", page)

    def test_only_cases_in_technical_review_and_valid_decisions(self):
        with self.assertRaisesRegex(ValueError, "ya no espera una decisión"):
            quotes.resolve_technical_review("t-ok", "sufficient")
        with self.assertRaisesRegex(ValueError, "No entendimos esa opción"):
            quotes.resolve_technical_review("t-tech", "otra")
        with self.assertRaisesRegex(ValueError, "ya no existe"):
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
        from tests.test_app import FakeGmail, encoded_message
        from anka.servicios import case_actions
        quotes.resolve_technical_review("t-tech", "sufficient")
        gmail = FakeGmail({"id": "t-tech", "messages": [
            encoded_message("m-1", "cliente@empresa.co", "Solicito cotización.", 1, subject="Solicitud")]})
        with patch("anka.servicios.case_actions.load_config", return_value=test_config()), patch(
            "anka.servicios.case_actions.services", return_value=(gmail, object())
        ):
            case_actions.send_dashboard_reply("t-tech", "Hola, recibimos su solicitud.")
        quote = self.quote()
        self.assertEqual(quote["status"], QuoteStatus.READY_REVIEW)
        self.assertTrue(quote["sent_at"])
        page = pantalla_caso.render_case("t-tech")
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
        with self.assertLogs("anka.web.server", "WARNING"):
            denied = self.request("/review-decision", b"thread_id=t-tech&decision=sufficient&csrf_token=malo")
        self.assertEqual(denied["status"], 403)
        self.assertEqual(self.quote()["status"], QuoteStatus.REVIEW)
        ok = self.request(
            "/review-decision", f"thread_id=t-tech&decision=sufficient&csrf_token={seguridad.CSRF_TOKEN}".encode())
        self.assertEqual(ok["status"], 303)
        self.assertTrue(ok["location"].startswith("/case?thread_id=t-tech&message="))
        self.assertEqual(self.quote()["status"], QuoteStatus.READY_REVIEW)

    def test_post_with_missing_questions_shows_the_guidance_not_an_error_page(self):
        sent = self.request(
            "/review-decision", f"thread_id=t-tech&decision=ask_client&csrf_token={seguridad.CSRF_TOKEN}".encode())
        self.assertEqual(sent["status"], 303)
        self.assertIn("pregunta", sent["location"])


    def test_una_sola_seccion_de_decision_sin_repetir_preguntas_ni_respuesta_en_pausa(self):
        # Cubre: U-12
        page = pantalla_caso.render_case("t-tech")
        self.assertIn("Revisa y decide cómo seguir", page)
        self.assertNotIn("Respuesta en pausa", page)
        self.assertNotIn('id="respuesta"', page)
        self.assertIn("Con esto alcanza", page)
        self.assertIn("Hay que preguntarle al cliente", page)

    def test_las_preguntas_para_el_cliente_se_cuentan_en_vez_de_repetirse(self):
        # Cubre: U-12
        from anka.web.views import review_tasks_panel
        auditoria = [{"disposition": "ask_client", "unresolved": "Método de levantamiento"},
                     {"disposition": "internal_review", "question": "Revisar el plano CAD"}]
        repetido = str(review_tasks_panel(auditoria))
        self.assertIn("Pregunta sugerida al cliente", repetido)
        contado = str(review_tasks_panel(auditoria, questions_in_decision=True))
        self.assertNotIn("Pregunta sugerida al cliente", contado)
        self.assertNotIn("Método de levantamiento", contado)
        self.assertIn("1 pregunta(s) para el cliente", contado)
        self.assertIn("Revisar el plano CAD", contado)               # lo interno sigue a la vista (D-36)

    def test_tras_decidir_se_llega_directo_al_paso_que_sigue(self):
        # Cubre: U-12
        pregunta = self.request(
            "/review-decision",
            f"thread_id=t-tech&decision=ask_client&questions=%C2%BFQu%C3%A9%3F&csrf_token={seguridad.CSRF_TOKEN}".encode())
        self.assertTrue(pregunta["location"].endswith("#respuesta"))
        page = pantalla_caso.render_case("t-tech")
        self.assertIn("Revisa el correo y envíalo", page)
        self.assertIn('href="#respuesta"', page)
        self.assertIn("Nada sale al cliente hasta que pulses", page)

    def test_si_la_informacion_basta_lleva_a_emitir_la_t(self):
        # Cubre: U-12
        suficiente = self.request(
            "/review-decision", f"thread_id=t-tech&decision=sufficient&csrf_token={seguridad.CSRF_TOKEN}".encode())
        self.assertTrue(suficiente["location"].endswith("#emitir-t"))


if __name__ == "__main__":
    unittest.main()
