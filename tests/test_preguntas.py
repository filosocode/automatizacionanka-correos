"""Decidir cada pregunta y saber de qué correos se espera respuesta (U-22). Cubre: U-22"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio import preguntas as dominio
from anka.dominio import reception
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import case_actions, quotes
from anka.web import paginas, pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import FakeGmail, complete_analysis, encoded_message, test_config

# El caso real que originó U-22: Work dejó dos preguntas sin pasar el filtro y tres tareas internas.
ACUSE = "Buen día,\n\nGracias por compartir la solicitud. Hemos recibido la información para revisión técnica."


def analisis_de_work() -> dict:
    analisis = complete_analysis()
    analisis["attachments"] = [{"filename": "parque.kmz", "status": "bloqueado", "detail": "KMZ sin abrir"}]
    analisis["question_audit"] = [
        {"question": "¿Cuál es el contenido del KMZ?", "disposition": "internal_review", "reason_missing": "x",
         "reviewed_sources": ["Correo"], "unresolved": "Abrir el KML interno"},
        {"question": "¿Qué densidad y precisión LiDAR requieren?", "disposition": "do_not_ask",
         "reason_missing": "x", "reviewed_sources": ["Correo"], "unresolved": "Definirlo en la oferta"},
    ]
    analisis["other_questions"] = [
        "Inspección interna del KMZ: archivo interno, geometría y atributos (critical)",
        "Definición técnica interna de densidad y precisión LiDAR (recommended)",
        "Confirmación interna de identidad por diferencia entre encabezado y firma (recommended)",
    ]
    analisis["work_reply"] = {"text": ACUSE, "draft_id": "d-work"}
    return analisis


class DominioTests(unittest.TestCase):
    def test_internal_tasks_are_recognised_in_masculine_feminine_and_adverb(self):
        for texto in ("Inspección interna del KMZ: archivo interno", "Definición técnica interna de densidad",
                      "Crítico interno: revisar el KML", "Validar internamente el CRS", "Revisión de los planos internos"):
            with self.subTest(texto=texto):
                self.assertTrue(dominio.es_interna(texto))

    def test_a_real_question_to_the_client_is_never_treated_as_internal(self):
        self.assertFalse(dominio.es_interna("¿Cuál es el área interna del lote?"))
        self.assertFalse(dominio.es_interna("¿Qué datum usarán?"))
        self.assertFalse(dominio.es_interna("Plazo de entrega: confirmar internamente"))  # «internamente» tras los dos puntos

    def test_analysis_marks_are_removed_and_the_first_letter_is_capitalised(self):
        self.assertEqual(dominio.limpiar("Crítico: confirmar el datum (critical)"), "Confirmar el datum")
        self.assertEqual(dominio.limpiar("recomendable: definir el plazo (recommended)"), "Definir el plazo")
        self.assertEqual(dominio.limpiar(None), "")

    def test_the_identifier_ignores_case_accents_and_spacing(self):
        self.assertEqual(dominio.identificador("¿Cuál es el  ÁREA?"), dominio.identificador("¿cual es el area?"))
        self.assertNotEqual(dominio.identificador("área"), dominio.identificador("longitud"))

    def test_candidates_from_the_real_case_suggest_nothing_for_the_client(self):
        candidatas = dominio.candidatas(analisis_de_work(), {})
        self.assertEqual(len(candidatas), 5)
        self.assertEqual({c["sugerida"] for c in candidatas}, {dominio.INTERNA, dominio.DESCARTAR})
        por_texto = {c["texto"]: c["sugerida"] for c in candidatas}
        self.assertEqual(por_texto["¿Qué densidad y precisión LiDAR requieren?"], dominio.DESCARTAR)
        self.assertEqual(por_texto["¿Cuál es el contenido del KMZ?"], dominio.INTERNA)

    def test_candidates_are_not_repeated_across_sources(self):
        analisis = {"question_audit": [{"question": "¿Cuál es el datum?", "disposition": "ask_client"}],
                    "other_questions": ["Crítico: ¿cuál es el datum? (critical)", "¿Cuál es el datum?"]}
        candidatas = dominio.candidatas(analisis, {"questions": ["¿CUÁL ES EL DATUM?"]})
        self.assertEqual(len(candidatas), 1)
        self.assertEqual(candidatas[0]["sugerida"], dominio.PREGUNTAR)

    def test_reception_questions_are_candidates_for_the_client(self):
        candidatas = dominio.candidatas({}, {"questions": ["¿Cuál es el área del trabajo?"]})
        self.assertEqual([(c["origen"], c["sugerida"]) for c in candidatas], [("recepción", dominio.PREGUNTAR)])

    def test_deciding_applies_each_choice_and_adds_the_persons_own_questions(self):
        actuales = dominio.con_decisiones(dominio.candidatas(analisis_de_work(), {}), None)
        lidar = next(c for c in actuales if "LiDAR requieren" in c["texto"])
        kmz = next(c for c in actuales if "contenido del KMZ" in c["texto"])
        decididas = dominio.decidir(
            actuales, {lidar["id"]: (dominio.PREGUNTAR, "¿Qué precisión LiDAR esperan, en cm?"),
                       kmz["id"]: (dominio.INTERNA, kmz["texto"])},
            ["¿Hay acceso al predio?", "  ", "¿Hay acceso al predio?"], "dashboard", "2026-10-08T15:00:00-05:00")
        self.assertEqual(dominio.para_el_cliente(decididas), ["¿Qué precisión LiDAR esperan, en cm?",
                                                              "¿Hay acceso al predio?"])  # sin repetir ni vacías
        self.assertIn("¿Cuál es el contenido del KMZ?", dominio.internas(decididas))
        propia = next(p for p in decididas if p["origen"] == "persona")
        self.assertEqual((propia["decision"], propia["por"]), (dominio.PREGUNTAR, "dashboard"))

    def test_an_unknown_decision_and_too_many_questions_are_rejected(self):
        actuales = dominio.con_decisiones(dominio.candidatas(analisis_de_work(), {}), None)
        with self.assertRaises(ValueError):
            dominio.decidir(actuales, {actuales[0]["id"]: ("mandar-sms", "x")}, [], "dashboard", "ahora")
        with self.assertRaises(ValueError):
            dominio.decidir([], {}, [f"¿Pregunta número {n}?" for n in range(dominio.MAX_PREGUNTAS + 1)],
                            "dashboard", "ahora")

    def test_saved_decisions_come_back_with_their_edited_text_and_the_persons_questions(self):
        actuales = dominio.con_decisiones(dominio.candidatas(analisis_de_work(), {}), None)
        decididas = dominio.decidir(actuales, {actuales[0]["id"]: (dominio.DESCARTAR, "Texto editado")},
                                    ["¿Mi pregunta?"], "dashboard", "ahora")
        de_nuevo = dominio.con_decisiones(dominio.candidatas(analisis_de_work(), {}), decididas)
        self.assertEqual((de_nuevo[0]["texto"], de_nuevo[0]["decision"]), ("Texto editado", dominio.DESCARTAR))
        self.assertEqual(dominio.elegida(de_nuevo[0]), dominio.DESCARTAR)
        self.assertEqual(dominio.elegida(de_nuevo[1]), de_nuevo[1]["sugerida"])  # sin decidir: manda la sugerencia
        self.assertIn("¿Mi pregunta?", dominio.para_el_cliente(de_nuevo))

    def test_the_questions_in_a_sent_email_are_detected(self):
        self.assertEqual(dominio.en_el_correo(ACUSE), [])
        cuerpo = "Hola,\n\n1. ¿Cuál es el área?\n2. ¿Hay acceso al predio?\n\nSaludos"
        self.assertEqual(dominio.en_el_correo(cuerpo), ["¿Cuál es el área?", "¿Hay acceso al predio?"])
        self.assertEqual(dominio.en_el_correo("What is the datum? What is the datum?"), ["What is the datum?"])

    def test_links_with_a_query_string_are_not_questions(self):
        self.assertEqual(dominio.en_el_correo("Vea https://anka.com.co/ficha?id=7&x=1 y www.anka.com.co/a?b=2"), [])


class SugerenciaDeRespuestaTests(unittest.TestCase):
    def test_a_decision_makes_only_the_chosen_questions_go_out(self):
        analisis = complete_analysis(location="")
        analisis["preguntas"] = [
            {"id": "a", "texto": "¿Cuál es el datum?", "decision": dominio.PREGUNTAR},
            {"id": "b", "texto": "Revisar el KMZ", "decision": dominio.INTERNA},
            {"id": "c", "texto": "¿Cuál es el plazo?", "decision": dominio.DESCARTAR},
        ]
        respuesta = reception.suggested_reply(analisis, ["ubicación del proyecto"])
        self.assertIn("1. ¿Cuál es el datum?", respuesta)
        self.assertNotIn("Revisar el KMZ", respuesta)
        self.assertNotIn("¿Cuál es el plazo?", respuesta)

    def test_deciding_to_ask_nothing_gives_the_plain_acknowledgement(self):
        analisis = complete_analysis(location="")
        analisis["preguntas"] = [{"id": "a", "texto": "¿Cuál es el datum?", "decision": dominio.INTERNA}]
        respuesta = reception.suggested_reply(analisis, ["ubicación del proyecto"])
        self.assertEqual(dominio.en_el_correo(respuesta), [])
        self.assertIn("Confirmamos que la hemos recibido", respuesta)

    def test_without_decisions_the_suggestion_is_the_one_from_before(self):
        analisis = complete_analysis(location="")
        self.assertIn("¿podría confirmar lo siguiente?", reception.suggested_reply(analisis, ["ubicación del proyecto"]))


class CasoCompletoTests(unittest.TestCase):
    """El caso de Candelaria de principio a fin: decidir, armar el correo, enviar y ver el estado."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (
            patch.object(settings, "DATA_DIR", Path(self.temp.name)),
            patch("anka.web.paginas.load_config", return_value=test_config()),
            patch("anka.web.pantalla_caso.load_config", return_value=test_config()),
            patch("anka.servicios.case_actions.load_config", return_value=test_config()),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis_de_work())
        db.close()

    def caso(self):
        return quotes.get_quote("t-1")

    def enviar(self, texto):
        gmail = FakeGmail({"id": "t-1", "messages": [
            encoded_message("m-1", "cliente@empresa.co", "Solicito cotización.", 1, subject="Solicitud")]})
        with patch("anka.servicios.case_actions.services", return_value=(gmail, object())):
            return case_actions.send_dashboard_reply("t-1", texto)

    def elegir(self, **por_texto):
        candidatas = dominio.con_decisiones(dominio.candidatas(self.caso()["analysis"], {}), None)
        elecciones = {c["id"]: (por_texto.get(c["texto"], (dominio.INTERNA, c["texto"]))[0],
                                por_texto.get(c["texto"], (None, c["texto"]))[1]) for c in candidatas}
        return elecciones

    def test_the_page_lists_every_candidate_and_suggests_nothing_for_the_client(self):
        pagina = pantalla_caso.render_case("t-1")
        self.assertEqual(pagina.count('<div class="pregunta">'), 5)
        self.assertNotIn('value="ask_client" selected', pagina)
        self.assertIn("Guardar mis decisiones sin preguntar al cliente", pagina)
        self.assertIn("Agregar mis propias preguntas", pagina)

    def test_choosing_questions_builds_the_email_with_them_instead_of_the_work_acknowledgement(self):
        lidar = "¿Qué densidad y precisión LiDAR requieren?"
        quotes.resolve_technical_review(
            "t-1", "ask_client", elecciones=self.elegir(**{lidar: (dominio.PREGUNTAR, "¿Qué precisión LiDAR esperan?")}),
            nuevas="¿Hay acceso al predio?")
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.REVIEW_PENDING)
        self.assertEqual(caso["analysis"]["manual_review"]["questions"],
                         ["¿Qué precisión LiDAR esperan?", "¿Hay acceso al predio?"])
        caja = pantalla_caso.render_case("t-1").split('name="body"')[1].split("</textarea>")[0]
        self.assertIn("1. ¿Qué precisión LiDAR esperan?", caja)
        self.assertIn("2. ¿Hay acceso al predio?", caja)
        self.assertNotIn("Hemos recibido la información para revisión técnica", caja)  # no manda el acuse de Work
        db = store.connect()
        try:
            evento = db.execute("SELECT summary FROM case_events WHERE thread_id='t-1' AND kind='review_decision'"
                                ).fetchone()[0]
        finally:
            db.close()
        self.assertIn("Decidiste preguntar al cliente (2 pregunta(s); 4 por resolver dentro de Anka)", evento)

    def test_sending_questions_leaves_the_case_waiting_with_their_count(self):
        quotes.resolve_technical_review("t-1", "ask_client", nuevas="¿Hay acceso al predio?\n¿Cuál es el datum?",
                                        elecciones=self.elegir())
        self.enviar("Hola,\n\n1. ¿Hay acceso al predio?\n2. ¿Cuál es el datum?\n\nSaludos")
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.WAITING)
        self.assertEqual(len(caso["analysis"]["preguntas_enviadas"]["preguntas"]), 2)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Esperamos su respuesta a 2 preguntas", pagina)

    def test_sending_an_acknowledgement_does_not_leave_the_case_waiting(self):
        # el defecto real: se «preguntó» por tareas internas, salió el acuse y el caso quedó esperando al cliente
        quotes.resolve_technical_review("t-1", "sufficient", elecciones=self.elegir())
        db = store.connect()
        db.execute("UPDATE quotes SET status=?", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        self.enviar(ACUSE)
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.RESPONSE_SENT)
        self.assertEqual(caso["analysis"]["preguntas_enviadas"]["preguntas"], [])
        self.assertIn("No se espera respuesta del cliente a este correo", pantalla_caso.render_case("t-1"))

    def test_the_event_and_the_notice_say_whether_an_answer_is_expected(self):
        self.enviar(ACUSE)
        db = store.connect()
        try:
            resumen = db.execute("SELECT summary FROM case_events WHERE thread_id='t-1' AND kind='sent'").fetchone()[0]
            aviso = db.execute(
                "SELECT * FROM dashboard_notifications WHERE thread_id='t-1' AND title='Respuesta enviada'").fetchone()
        finally:
            db.close()
        self.assertIn("sin preguntas: no se espera respuesta del cliente", resumen)
        self.assertIn("sin preguntas", " ".join(str(v) for v in dict(aviso).values()))

    def test_saving_decisions_keeps_the_review_open_and_puts_the_internal_ones_in_the_pending_list(self):
        quotes.resolve_technical_review("t-1", "internal_only", elecciones=self.elegir())
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.REVIEW)           # la revisión técnica sigue abierta
        self.assertNotIn("manual_review", caso["analysis"])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Revisión interna pendiente", pagina)
        self.assertIn("Lo decidiste resolver dentro de Anka", pagina)
        self.assertIn(" decidida por ti", pagina)                       # las tarjetas recuerdan lo decidido

    def test_discarded_questions_disappear_from_the_pending_list(self):
        elecciones = {c["id"]: (dominio.DESCARTAR, c["texto"])
                      for c in dominio.con_decisiones(dominio.candidatas(self.caso()["analysis"], {}), None)}
        quotes.resolve_technical_review("t-1", "internal_only", elecciones=elecciones)
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn("Lo decidiste resolver dentro de Anka", pagina)

    def test_choosing_ask_client_without_any_question_is_rejected_with_guidance(self):
        with self.assertRaisesRegex(ValueError, "al menos una pregunta"):
            quotes.resolve_technical_review("t-1", "ask_client", elecciones=self.elegir())
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW)
        with self.assertRaisesRegex(ValueError, "Elige qué hacer"):
            quotes.resolve_technical_review("t-1", "internal_only")

    def test_the_old_one_line_per_question_form_still_works(self):
        quotes.resolve_technical_review("t-1", "ask_client", "¿Datum?\n¿Plazo?")
        self.assertEqual(self.caso()["analysis"]["manual_review"]["questions"], ["¿Datum?", "¿Plazo?"])

    def hace_30_dias(self):
        db = store.connect()
        db.execute("UPDATE quotes SET sent_at='2026-09-08T10:00:00-05:00'")
        db.commit()
        db.close()

    def test_a_case_that_got_only_an_acknowledgement_never_shows_waiting_days_or_call_alerts(self):
        db = store.connect()
        db.execute("UPDATE quotes SET status=?", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        self.enviar(ACUSE)
        self.hace_30_dias()                                            # mucho más que los días de aviso y de llamada
        caso = self.caso()
        self.assertEqual(caso["status"], QuoteStatus.RESPONSE_SENT)
        self.assertEqual(caso["waiting_since"], "")
        etapa, _ = paginas.case_stage(caso)
        self.assertEqual(etapa, "Respuesta enviada · sin espera")
        self.assertNotIn("Llamar", etapa)
        self.assertNotIn("Sin respuesta", etapa)

    def test_a_case_that_asked_questions_does_wait_and_ends_up_asking_to_call(self):
        db = store.connect()
        db.execute("UPDATE quotes SET status=?", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        self.enviar("Hola,\n\n1. ¿Hay acceso al predio?\n\nSaludos")
        self.assertTrue(self.caso()["waiting_since"])
        self.hace_30_dias()
        etapa, urgente = paginas.case_stage(self.caso())
        self.assertEqual(etapa, "Llamar al cliente")
        self.assertTrue(urgente)

    def test_cases_from_before_this_change_keep_their_old_behaviour(self):
        db = store.connect()
        db.execute("UPDATE quotes SET status=?, sent_at='2026-10-01T10:00:00-05:00'", (QuoteStatus.RESPONSE_SENT,))
        db.commit()
        db.close()
        self.assertTrue(self.caso()["waiting_since"])                    # sin el dato de preguntas, no se asume nada

    def peticion(self, cuerpo):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        enviado = {}
        crudo = cuerpo.encode()
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = "/review-decision"
        handler.rfile = io.BytesIO(crudo)
        handler._send_html = lambda contenido, status=200: enviado.update(status=status, page=contenido)
        handler._redirect = lambda destino: enviado.update(status=303, location=destino)
        handler.do_POST()
        return enviado

    def test_the_form_posts_one_choice_per_question(self):
        candidatas = dominio.con_decisiones(dominio.candidatas(self.caso()["analysis"], {}), None)
        campos = [("thread_id", "t-1"), ("decision", "ask_client"), ("csrf_token", seguridad.CSRF_TOKEN),
                  ("q_nuevas", "¿Hay acceso al predio?")]
        for c in candidatas:
            campos += [("q_id", c["id"]), (f"q_decision_{c['id']}", "internal"), (f"q_text_{c['id']}", c["texto"])]
        respuesta = self.peticion(urllib.parse.urlencode(campos))
        self.assertEqual(respuesta["status"], 303)
        self.assertTrue(respuesta["location"].endswith("#respuesta"))
        self.assertEqual(self.caso()["analysis"]["manual_review"]["questions"], ["¿Hay acceso al predio?"])

    def test_saving_decisions_through_the_form_stays_on_the_case(self):
        candidatas = dominio.con_decisiones(dominio.candidatas(self.caso()["analysis"], {}), None)
        campos = [("thread_id", "t-1"), ("decision", "internal_only"), ("csrf_token", seguridad.CSRF_TOKEN)]
        for c in candidatas:
            campos += [("q_id", c["id"]), (f"q_decision_{c['id']}", "discard"), (f"q_text_{c['id']}", c["texto"])]
        respuesta = self.peticion(urllib.parse.urlencode(campos))
        self.assertEqual(respuesta["status"], 303)
        self.assertNotIn("#respuesta", respuesta["location"])  # guardar decisiones no cambia de paso
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW)


if __name__ == "__main__":
    unittest.main()
