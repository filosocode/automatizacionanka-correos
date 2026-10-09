"""Armar el correo con las preguntas que la persona escribe, en cualquier etapa. Cubre: U-24"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import preguntas as dominio
from anka.dominio.reception import correo_con_preguntas, suggested_reply
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.web import openapi, pantalla_caso
from tests.test_app import complete_analysis, test_config


class SepararPreguntas(unittest.TestCase):
    def test_each_line_is_a_question_without_numbering_or_bullets_and_without_repeats(self):
        texto = "1. ¿Cuál es el datum?\n- Plazo de entrega\n  \n2) ¿Cuál es el datum?\n* Hay acceso al predio."
        self.assertEqual(dominio.separar(texto), ["¿Cuál es el datum?", "¿Plazo de entrega?", "¿Hay acceso al predio?"])

    def test_a_line_that_is_already_a_question_is_left_alone(self):
        self.assertEqual(dominio.separar("What is the datum?"), ["What is the datum?"])

    def test_there_is_a_maximum_and_nothing_to_ask_gives_an_empty_list(self):
        self.assertEqual(len(dominio.separar("\n".join(f"Pregunta {n}" for n in range(50)))), dominio.MAX_PREGUNTAS)
        self.assertEqual(dominio.separar("  \n\n"), [])
        self.assertEqual(dominio.separar(""), [])

    def test_a_very_long_line_is_cut(self):
        self.assertLessEqual(len(dominio.separar("x" * 2000)[0]), dominio.MAX_TEXTO + 2)


class PlantillaDelCorreo(unittest.TestCase):
    def test_the_manual_email_uses_the_same_template_as_the_suggested_one(self):
        # Cubre: U-24
        preguntas = ["¿Cuál es el datum?", "¿Hay acceso al predio?"]
        manual = correo_con_preguntas(preguntas)
        analisis = complete_analysis(location="")
        analisis["preguntas"] = [{"id": "a", "texto": p, "decision": "ask_client"} for p in preguntas]
        sugerido = suggested_reply(analisis, [])
        self.assertIn("1. ¿Cuál es el datum?\n2. ¿Hay acceso al predio?", manual)
        self.assertIn("Puede responder a este mismo correo.", manual)
        self.assertTrue(manual.startswith("Hola,\n\nGracias por su solicitud."))
        self.assertTrue(manual.endswith("Saludos,\nGrupo Anka"))
        self.assertEqual(manual.split("1. ")[0], sugerido.split("1. ")[0])

    def test_the_questions_in_it_are_the_ones_the_server_counts_to_decide_if_an_answer_is_expected(self):
        # Cubre: U-24
        correo = correo_con_preguntas(dominio.separar("Plazo de entrega\nEnvíe el KMZ"))
        self.assertEqual(len(dominio.en_el_correo(correo)), 2)

    def test_the_phrase_that_introduces_the_list_is_not_counted_as_a_question(self):
        # Cubre: U-24
        correo = correo_con_preguntas(["¿Cuál es el datum?", "¿Hay acceso al predio?", "¿Plazo?"])
        self.assertEqual(dominio.en_el_correo(correo), ["¿Cuál es el datum?", "¿Hay acceso al predio?", "¿Plazo?"])
        self.assertEqual(dominio.en_el_correo("Hola, ¿podría confirmar lo siguiente?"), [])

    def test_a_forwarded_request_gets_the_forwarded_opening(self):
        # Cubre: U-24
        self.assertIn("Gracias por compartir la solicitud.", correo_con_preguntas(["¿Datum?"], reenviado=True))


class EnLaPantalla(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def caso(self, estado, codigo=False):
        db = store.open_db(Path(self.temp.name))
        db.execute("DELETE FROM quotes")
        store.save(db, "t-1", "m-1", estado, "cliente@empresa.co", complete_analysis())
        if codigo:
            db.execute("UPDATE quotes SET quote_code='26145', code_stage='T'")
        db.commit()
        db.close()

    def cuadro(self, pagina):
        return pagina.split('name="body"')[1].split("</textarea>")[0].split(">", 1)[1]

    def test_the_box_is_offered_in_every_stage_where_you_can_write_to_the_client(self):
        # Cubre: U-24
        casos = [(QuoteStatus.REVIEW_PENDING, False, ""), (QuoteStatus.READY_REVIEW, False, ""),
                 (QuoteStatus.READY, True, ""), (QuoteStatus.WAITING, False, "libre")]
        for estado, codigo, plantilla in casos:
            with self.subTest(estado=estado):
                self.caso(estado, codigo)
                pagina = pantalla_caso.render_case("t-1", template=plantilla)
                self.assertIn("Armar correo con estas preguntas", pagina)
                self.assertIn('name="preguntas"', pagina)

    def test_asking_for_the_email_fills_the_box_with_the_template_and_keeps_what_was_typed(self):
        # Cubre: U-24
        self.caso(QuoteStatus.READY_REVIEW)
        pagina = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto="Plazo de entrega\nEnvíe el KMZ")
        cuadro = self.cuadro(pagina)
        self.assertIn("1. ¿Plazo de entrega?", cuadro)
        self.assertIn("2. ¿Envíe el KMZ?", cuadro)
        self.assertIn("Plazo de entrega", pagina.split('name="preguntas"')[1].split("</textarea>")[0])

    def test_with_no_questions_typed_the_box_stays_blank(self):
        # Cubre: U-24
        self.caso(QuoteStatus.READY_REVIEW)
        pagina = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto="  ")
        self.assertNotIn("podría confirmar", self.cuadro(pagina))

    def test_what_is_typed_is_escaped(self):
        # Cubre: U-24
        self.caso(QuoteStatus.READY_REVIEW)
        pagina = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", pagina)

    def test_nothing_is_sent_or_changed_by_asking_for_the_email(self):
        # Cubre: U-24
        self.caso(QuoteStatus.READY_REVIEW)
        with patch("anka.servicios.case_actions.services", side_effect=AssertionError("no debe tocar Gmail")):
            pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto="¿Datum?")
        db = store.open_db(Path(self.temp.name))
        try:
            estado = db.execute("SELECT status FROM quotes").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(estado, QuoteStatus.READY_REVIEW)

    def test_the_form_field_is_documented_in_the_api(self):
        # Cubre: U-24
        campos = [campo.nombre for operacion in openapi.OPERACIONES if operacion.ruta == "/case"
                  for campo in operacion.campos]
        self.assertIn("preguntas", campos)


if __name__ == "__main__":
    unittest.main()
