"""Cambiar de idea antes de enviar las preguntas. Cubre: U-27"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio import preguntas as dominio
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.web import pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config

NOTA = "KML no inspeccionable; geometría/cobertura no verificadas."
PUNTO = "¿Cuál es el datum del levantamiento?"


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        analisis = complete_analysis(location="")
        analisis["other_questions"] = [NOTA, PUNTO]
        # una contradicción pendiente deja el caso en revisión técnica, que es donde se ofrecen los selectores
        analisis["contradictions"] = [{"topic": "área", "details": "10 ha frente a 100 ha", "sources": [], "status": "pendiente"}]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.close()

    def elegir(self, decisiones):
        """Prepara el correo con estas decisiones {texto: decisión}: el caso queda en «por aclarar»."""
        candidatas = dominio.con_decisiones(dominio.candidatas(quotes.get_quote("t-1")["analysis"], {}), None)
        elecciones = {c["id"]: (decisiones.get(c["texto"], dominio.INTERNA), c["texto"]) for c in candidatas}
        return quotes.resolve_technical_review("t-1", "ask_client", elecciones=elecciones)

    def caso(self):
        return quotes.get_quote("t-1")

    def eventos(self):
        db = store.connect()
        try:
            return [fila["summary"] for fila in db.execute("SELECT summary FROM case_events WHERE thread_id='t-1' AND kind='review_decision'")]
        finally:
            db.close()


class VolverAtras(Base):
    def test_going_back_returns_to_the_technical_decision_and_keeps_every_choice(self):
        self.elegir({PUNTO: dominio.PREGUNTAR, NOTA: dominio.DESCARTAR})
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW_PENDING)
        antes = {p["texto"]: p.get("decision") for p in self.caso()["analysis"]["preguntas"]}
        mensaje = quotes.volver_a_decidir("t-1", "reconsiderar")
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.REVIEW)
        self.assertNotIn("manual_review", caso["analysis"])
        self.assertEqual({p["texto"]: p.get("decision") for p in caso["analysis"]["preguntas"]}, antes)
        self.assertIn("elegir las preguntas", mensaje)
        self.assertTrue(any("Volviste a la decisión técnica" in e for e in self.eventos()))

    def test_after_going_back_the_selectors_come_back_with_what_was_chosen(self):
        self.elegir({PUNTO: dominio.PREGUNTAR, NOTA: dominio.DESCARTAR})
        quotes.volver_a_decidir("t-1", "reconsiderar")
        pagina = pantalla_caso.render_case("t-1")
        tarjetas = [t.split("</select>")[0] for t in pagina.split('<div class="pregunta">')[1:]]
        elegida = {nota: next(t for t in tarjetas if nota[:20] in t) for nota in (NOTA, PUNTO)}
        self.assertIn('value="ask_client" selected', elegida[PUNTO])
        self.assertIn('value="discard" selected', elegida[NOTA])

    def test_the_person_can_then_choose_other_questions_and_prepare_the_email_again(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        quotes.volver_a_decidir("t-1", "reconsiderar")
        self.elegir({PUNTO: dominio.PREGUNTAR, NOTA: dominio.PREGUNTAR})
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW_PENDING)
        self.assertEqual(len(self.caso()["analysis"]["manual_review"]["questions"]), 2)

    def test_giving_up_on_asking_marks_the_information_as_sufficient(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        mensaje = quotes.volver_a_decidir("t-1", "suficiente")
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.READY_REVIEW)
        self.assertEqual(caso["analysis"]["manual_review"]["decision"], "sufficient")
        self.assertEqual(caso["analysis"]["manual_review"]["questions"], [])
        self.assertIn("suficiente", mensaje)
        self.assertTrue(any("no preguntar" in e.lower() for e in self.eventos()))

    def test_nothing_is_sent_by_going_back(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        with patch("anka.servicios.case_actions.services", side_effect=AssertionError("no debe tocar Gmail")):
            quotes.volver_a_decidir("t-1", "reconsiderar")

    def test_a_draft_saved_in_gmail_is_not_deleted(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        db = store.connect()
        db.execute("UPDATE quotes SET status=?, draft_id='draft-1', draft_body='texto' WHERE thread_id='t-1'",
                   (QuoteStatus.DRAFT_CREATED,))
        db.commit()
        db.close()
        mensaje = quotes.volver_a_decidir("t-1", "reconsiderar")
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW)
        self.assertEqual(self.caso()["draft_id"], "draft-1")
        self.assertIn("borrador", mensaje.lower())


class NoSePuede(Base):
    def test_once_the_email_went_out_it_cannot_be_taken_back(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        for estado in (QuoteStatus.WAITING, QuoteStatus.RESPONSE_SENT):
            with self.subTest(estado=estado):
                db = store.connect()
                db.execute("UPDATE quotes SET status=?, sent_at='2099-01-01T00:00:00-05:00' WHERE thread_id='t-1'", (estado,))
                db.commit()
                db.close()
                with self.assertRaisesRegex(ValueError, "no ha salido"):
                    quotes.volver_a_decidir("t-1", "reconsiderar")
                self.assertEqual(self.caso()["status"], estado)

    def test_a_sent_email_blocks_it_even_if_the_state_was_not_updated(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        db = store.connect()
        db.execute("UPDATE quotes SET sent_at='2099-01-01T00:00:00-05:00' WHERE thread_id='t-1'")
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError, "no ha salido"):
            quotes.volver_a_decidir("t-1", "suficiente")

    def test_without_a_previous_decision_to_ask_there_is_nothing_to_undo(self):
        db = store.connect()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='t-1'", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError, "no ha salido"):
            quotes.volver_a_decidir("t-1", "reconsiderar")

    def test_an_unknown_action_and_a_missing_case_are_refused(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        with self.assertRaisesRegex(ValueError, "no válida"):
            quotes.volver_a_decidir("t-1", "borrar-todo")
        with self.assertRaisesRegex(ValueError, "no existe"):
            quotes.volver_a_decidir("inexistente", "reconsiderar")


class EnLaPantalla(Base):
    def test_the_screen_offers_to_go_back_only_while_the_email_has_not_gone_out(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("¿Cambiaste de idea?", pagina)
        self.assertIn("Volver a elegir las preguntas", pagina)
        self.assertIn("No preguntar: la información es suficiente", pagina)
        self.assertIn('action="/review-reopen"', pagina)
        db = store.connect()
        db.execute("UPDATE quotes SET status=?, sent_at='2099-01-01T00:00:00-05:00' WHERE thread_id='t-1'", (QuoteStatus.WAITING,))
        db.commit()
        db.close()
        self.assertNotIn("¿Cambiaste de idea?", pantalla_caso.render_case("t-1"))

    def test_it_is_not_offered_before_any_decision(self):
        self.assertNotIn("¿Cambiaste de idea?", pantalla_caso.render_case("t-1"))

    def pedir(self, campos, token=True):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        datos = ({"csrf_token": seguridad.CSRF_TOKEN} if token else {}) | campos
        crudo = urllib.parse.urlencode(datos).encode()
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = "/review-reopen"
        handler.rfile = io.BytesIO(crudo)
        visto = {}
        handler._send_html = lambda contenido, status=200: visto.update(status=status)
        handler._redirect = lambda destino: visto.update(status=303, location=destino)
        handler.do_POST()
        return visto

    def test_the_form_goes_back_and_lands_on_the_right_step(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        respuesta = self.pedir({"thread_id": "t-1", "accion": "reconsiderar"})
        self.assertEqual(respuesta["status"], 303)
        self.assertTrue(respuesta["location"].endswith("#decision"))
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW)

    def test_giving_up_lands_on_issuing_the_code(self):
        self.elegir({PUNTO: dominio.PREGUNTAR})
        respuesta = self.pedir({"thread_id": "t-1", "accion": "suficiente"})
        self.assertTrue(respuesta["location"].endswith("#emitir-t"))
        self.assertEqual(self.caso()["status"], QuoteStatus.READY_REVIEW)

    def test_a_rejection_comes_back_with_its_reason_and_changes_nothing_and_the_form_needs_the_token(self):
        respuesta = self.pedir({"thread_id": "t-1", "accion": "reconsiderar"})        # todavía no hay nada que deshacer
        self.assertEqual(respuesta["status"], 303)
        self.assertIn("no%20ha%20salido", respuesta["location"])
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW)
        self.assertEqual(self.pedir({"thread_id": "t-1", "accion": "reconsiderar"}, token=False)["status"], 403)


if __name__ == "__main__":
    unittest.main()
