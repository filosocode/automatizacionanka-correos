"""Los puntos que se piden al cliente son los de su lista y lo que es de Anka no va. Cubre: U-26"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import preguntas as dominio
from anka.dominio.reception import correo_con_preguntas, suggested_reply
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.web import pantalla_caso
from tests.test_app import complete_analysis, test_config

# Lo que Work dejó realmente en `missing_data` de un caso.
LIMITE_1 = "KML no inspeccionable; geometría/cobertura no verificadas."
LIMITE_2 = "Gráfica del plano no inspeccionada; PDFs extraídos parcialmente."
PUNTO = "Nivel de detalle/entregable para redes y estructuras sin precisar."
TAREA = "Validar internamente precisión/control, GSD, permisos aeronáuticos y acceso."


def sugerencias(analisis):
    return {c["texto"]: c["sugerida"] for c in dominio.candidatas(analisis, {})}


class QueNoVaAlCliente(unittest.TestCase):
    def test_reading_limits_and_internal_tasks_are_for_anka(self):
        sugeridas = sugerencias({"other_questions": [LIMITE_1, LIMITE_2, PUNTO, TAREA]})
        self.assertEqual(sugeridas[dominio.limpiar(LIMITE_1)], dominio.INTERNA)
        self.assertEqual(sugeridas[dominio.limpiar(LIMITE_2)], dominio.INTERNA)
        self.assertEqual(sugeridas[dominio.limpiar(TAREA)], dominio.INTERNA)

    def test_a_point_the_client_can_confirm_is_still_suggested_for_the_client_without_a_question_mark(self):
        self.assertEqual(sugerencias({"other_questions": [PUNTO]})[dominio.limpiar(PUNTO)], dominio.PREGUNTAR)

    def test_a_real_question_is_still_suggested_for_the_client(self):
        sugeridas = sugerencias({"other_questions": ["¿Cuál es el datum del levantamiento?", "Crítico: ¿Plazo? (critical)"]})
        self.assertEqual(set(sugeridas.values()), {dominio.PREGUNTAR})

    def test_reading_limits_are_never_for_the_client_even_when_the_text_has_no_internal_word(self):
        for texto in ("KML no inspeccionable", "PDFs extraídos parcialmente", "Adjunto ilegible", "Dato no verificado"):
            with self.subTest(texto=texto):
                self.assertTrue(dominio.es_interna(texto))
        self.assertFalse(dominio.es_interna("¿Puede reenviar el KML? No se pudo abrir"))   # con «?» sigue siendo una pregunta

    def test_an_explicit_ask_client_from_the_audit_is_respected(self):
        analisis = {"question_audit": [{"question": "Datum del levantamiento", "disposition": "ask_client"}]}
        self.assertEqual(set(sugerencias(analisis).values()), {dominio.PREGUNTAR})


class CuentaDePuntos(unittest.TestCase):
    def test_the_points_of_the_list_are_what_the_email_asks_even_without_question_marks(self):
        correo = correo_con_preguntas([PUNTO, "Plazo de entrega esperado"])
        self.assertEqual(dominio.en_el_correo(correo), [PUNTO, "Plazo de entrega esperado"])

    def test_the_phrase_that_presents_the_list_is_the_question_not_one_more_point(self):
        self.assertEqual(dominio.en_el_correo("Hola, ¿podría confirmar lo siguiente?"), [])
        self.assertEqual(len(dominio.en_el_correo(correo_con_preguntas(["Uno", "Dos", "Tres"]))), 3)

    def test_the_code_template_and_a_free_email_are_counted_the_same_way(self):
        con_codigo = correo_con_preguntas(["Datum", "Plazo"], proyecto="X", codigo="T26145")
        self.assertEqual(dominio.en_el_correo(con_codigo), ["Datum", "Plazo"])
        libre = "Hola,\n\nPor favor confirme lo siguiente:\n\n1. Datum\n2) Plazo\n- Acceso\n\nSaludos"
        self.assertEqual(dominio.en_el_correo(libre), ["Datum", "Plazo", "Acceso"])

    def test_the_suggested_documents_are_not_points_to_confirm(self):
        correo = correo_con_preguntas(["Datum"], documentos=["Archivo KMZ", "Planos"])
        self.assertEqual(dominio.en_el_correo(correo), ["Datum"])

    def test_a_question_outside_the_list_counts_and_one_inside_it_counts_once(self):
        correo = correo_con_preguntas(["¿Cuál es el datum?"]) + "\n\n¿Podría enviar el KMZ?"
        self.assertEqual(len(dominio.en_el_correo(correo)), 2)

    def test_an_acknowledgement_has_no_points(self):
        self.assertEqual(dominio.en_el_correo("Hola,\n\nGracias por su solicitud. Confirmamos que la hemos recibido."), [])

    def test_the_email_built_from_the_chosen_points_lists_them_as_written_and_the_count_matches(self):
        analisis = complete_analysis(location="")
        analisis["preguntas"] = [dominio.Pregunta(id="a", texto=PUNTO, decision=dominio.PREGUNTAR),
                                 dominio.Pregunta(id="b", texto=LIMITE_1, decision=dominio.INTERNA)]
        correo = suggested_reply(analisis, [])
        self.assertIn("1. " + PUNTO, correo)
        self.assertNotIn("¿" + PUNTO, correo)                      # sin signos forzados
        self.assertNotIn("KML no inspeccionable", correo)
        self.assertEqual(dominio.en_el_correo(correo), [PUNTO])


class EnLaPantalla(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def test_the_selectors_start_on_resolve_internally_for_what_is_anka_s(self):
        analisis = complete_analysis(location="")
        analisis["other_questions"] = [LIMITE_1, LIMITE_2, PUNTO, TAREA]
        # una contradicción pendiente deja el caso en revisión técnica, que es donde se ofrecen los selectores
        analisis["contradictions"] = [{"topic": "área", "details": "10 ha frente a 100 ha", "sources": [], "status": "pendiente"}]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.close()
        pagina = pantalla_caso.render_case("t-1")
        tarjetas = [t.split("</fieldset>")[0] for t in pagina.split('<div class="pregunta">')[1:]]
        for nota, esperado in ((LIMITE_1, "internal"), (LIMITE_2, "internal"), (TAREA, "internal"), (PUNTO, "ask_client")):
            with self.subTest(nota=nota[:40]):
                tarjeta = next(t for t in tarjetas if nota.split(";")[0].split(".")[0] in t)
                self.assertIn(f'value="{esperado}" checked', tarjeta)


if __name__ == "__main__":
    unittest.main()
