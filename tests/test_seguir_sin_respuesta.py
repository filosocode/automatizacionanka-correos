"""Seguir con la T sin esperar la respuesta del cliente. Cubre: U-28"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.web import paginas, pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def caso(self, estado=QuoteStatus.WAITING, codigo=False):
        db = store.open_db(Path(self.temp.name))
        db.execute("DELETE FROM quotes")
        store.save(db, "t-1", "m-1", estado, "cliente@empresa.co", complete_analysis())
        db.execute("UPDATE quotes SET sent_at='2026-09-01T10:00:00-05:00'")
        if codigo:
            db.execute("UPDATE quotes SET quote_code='26145', code_stage='T'")
        db.commit()
        db.close()

    def leer(self):
        return quotes.get_quote("t-1")

    def eventos(self):
        db = store.connect()
        try:
            return [f["summary"] for f in db.execute("SELECT summary FROM case_events WHERE thread_id='t-1' AND kind='review_decision'")]
        finally:
            db.close()


class Servicio(Base):
    def test_a_case_waiting_for_the_client_goes_on_as_sufficient_by_the_persons_decision(self):
        self.caso()
        mensaje = quotes.seguir_sin_respuesta("t-1")
        caso = self.leer()
        self.assertEqual(caso["status"], QuoteStatus.READY_REVIEW)
        self.assertEqual(caso["analysis"]["manual_review"]["decision"], "sufficient")
        self.assertTrue(caso["analysis"]["manual_review"]["sin_respuesta"])
        self.assertIn("sin su respuesta", mensaje)
        self.assertTrue(any("sin esperar la respuesta del cliente" in e for e in self.eventos()))

    def test_an_answered_acknowledgement_case_can_also_go_on(self):
        self.caso(QuoteStatus.RESPONSE_SENT)
        quotes.seguir_sin_respuesta("t-1")
        self.assertEqual(self.leer()["status"], QuoteStatus.READY_REVIEW)

    def test_it_stops_waiting_and_stops_the_reminders(self):
        self.caso()
        self.assertTrue(self.leer()["waiting_since"])
        quotes.seguir_sin_respuesta("t-1")
        self.assertEqual(self.leer()["waiting_since"], "")
        db = store.connect()
        try:
            candidatos = db.execute("SELECT thread_id FROM quotes WHERE status='waiting' AND sent_at <> ''").fetchall()
        finally:
            db.close()
        self.assertEqual(candidatos, [])              # los recordatorios solo miran los casos esperando

    def test_nothing_is_sent(self):
        self.caso()
        with patch("anka.servicios.case_actions.services", side_effect=AssertionError("no debe tocar Gmail")):
            quotes.seguir_sin_respuesta("t-1")

    def test_a_case_with_a_code_or_not_waiting_is_refused_with_its_reason(self):
        self.caso(codigo=True)
        with self.assertRaisesRegex(ValueError, "espera"):
            quotes.seguir_sin_respuesta("t-1")
        for estado in (QuoteStatus.REVIEW, QuoteStatus.READY_REVIEW, QuoteStatus.REVIEW_PENDING):
            with self.subTest(estado=estado):
                self.caso(estado)
                with self.assertRaisesRegex(ValueError, "espera"):
                    quotes.seguir_sin_respuesta("t-1")
        with self.assertRaisesRegex(ValueError, "no existe"):
            quotes.seguir_sin_respuesta("inexistente")


class EnLaPantalla(Base):
    def pedir(self, campos, token=True):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        datos = ({"csrf_token": seguridad.CSRF_TOKEN} if token else {}) | campos
        crudo = urllib.parse.urlencode(datos).encode()
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = "/seguir-sin-respuesta"
        handler.rfile = io.BytesIO(crudo)
        visto = {}
        handler._send_html = lambda contenido, status=200: visto.update(status=status)
        handler._redirect = lambda destino: visto.update(status=303, location=destino)
        handler.do_POST()
        return visto

    def test_the_button_is_offered_while_waiting_and_after_an_acknowledgement_but_not_otherwise(self):
        for estado in (QuoteStatus.WAITING, QuoteStatus.RESPONSE_SENT):
            with self.subTest(estado=estado):
                self.caso(estado)
                pagina = pantalla_caso.render_case("t-1")
                self.assertIn("Seguir con la T sin su respuesta", pagina)
                self.assertIn('action="/seguir-sin-respuesta"', pagina)
        self.caso(QuoteStatus.REVIEW_PENDING)
        self.assertNotIn("Seguir con la T sin su respuesta", pantalla_caso.render_case("t-1"))
        self.caso(codigo=True)
        self.assertNotIn("Seguir con la T sin su respuesta", pantalla_caso.render_case("t-1"))

    def test_the_form_goes_on_and_lands_on_issuing_the_code_and_needs_the_token(self):
        self.caso()
        self.assertEqual(self.pedir({"thread_id": "t-1"}, token=False)["status"], 403)
        respuesta = self.pedir({"thread_id": "t-1"})
        self.assertEqual(respuesta["status"], 303)
        self.assertTrue(respuesta["location"].endswith("#emitir-t"))
        self.assertEqual(self.leer()["status"], QuoteStatus.READY_REVIEW)

    def test_the_list_no_longer_shows_it_as_waiting_and_asks_to_issue_the_code(self):
        self.caso()
        quotes.seguir_sin_respuesta("t-1")
        etapa, _ = paginas.case_stage(self.leer())
        self.assertEqual(etapa, "Listo para emitir la T")


if __name__ == "__main__":
    unittest.main()
