"""Desempeño del análisis medido con las correcciones del uso real. Cubre: T-35"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import desempeno as dom
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import desempeno as servicio
from anka.web import openapi, paginas
from anka.web import server as web
from tests.test_app import complete_analysis, test_config


def fila(actividad, cantidad="10", unidad="ha", estado="confirmado", ubicacion="Bogotá"):
    return {"activity": actividad, "quantity": cantidad, "unit": unidad, "estado": estado, "ubicacion": ubicacion}


class CambiosDeTabla(unittest.TestCase):
    def test_an_untouched_table_has_no_changes(self):
        tabla = [fila("Levantamiento LiDAR"), fila("Ortofoto")]
        c = dom.cambios_de_tabla(tabla, tabla)
        self.assertEqual((c["iniciales"], c["conservadas"], c["quitadas"], c["agregadas"]), (2, 2, 0, 0))
        self.assertEqual((c["cantidades_cambiadas"], c["estados_cambiados"], c["ubicaciones_cambiadas"]), (0, 0, 0))

    def test_removed_added_and_changed_rows_are_told_apart(self):
        inicial = [fila("Levantamiento LiDAR", "10"), fila("Ortofoto", "5"), fila("Batimetría", "2", "km")]
        final = [fila("Levantamiento LiDAR", "12"), fila("Ortofoto", "5", estado="pendiente"), fila("Informe final", "1", "und")]
        c = dom.cambios_de_tabla(inicial, final)
        self.assertEqual((c["conservadas"], c["quitadas"], c["agregadas"]), (2, 1, 1))
        self.assertEqual(c["cantidades_cambiadas"], 1)                  # el LiDAR pasó de 10 a 12
        self.assertEqual(c["estados_cambiados"], 1)                     # la ortofoto pasó a pendiente

    def test_matching_ignores_case_and_accents_and_the_unit_is_compared_in_its_canonical_form(self):
        c = dom.cambios_de_tabla([fila("Batimetría del río", "5", "Hectáreas")], [fila("BATIMETRIA DEL RIO", "5", "ha")])
        self.assertEqual((c["conservadas"], c["cantidades_cambiadas"]), (1, 0))

    def test_a_quantity_written_differently_but_equal_is_not_a_change(self):
        c = dom.cambios_de_tabla([fila("X", "1.250,5")], [fila("X", "1250,5")])
        self.assertEqual(c["cantidades_cambiadas"], 0)

    def test_repeated_activities_are_matched_one_to_one(self):
        c = dom.cambios_de_tabla([fila("Ortofoto", "5"), fila("Ortofoto", "7")], [fila("Ortofoto", "5")])
        self.assertEqual((c["conservadas"], c["quitadas"]), (1, 1))

    def test_empty_tables_do_not_break_it(self):
        c = dom.cambios_de_tabla([], [])
        self.assertEqual((c["iniciales"], c["conservadas"], c["agregadas"]), (0, 0, 0))


class Preguntas(unittest.TestCase):
    def test_agreement_is_counted_per_kind_of_change(self):
        preguntas = [
            {"origen": "análisis", "sugerida": "ask_client", "decision": "ask_client"},     # de acuerdo
            {"origen": "análisis", "sugerida": "ask_client", "decision": "internal"},       # la sacó del cliente
            {"origen": "análisis", "sugerida": "ask_client", "decision": "discard"},        # la sacó del cliente
            {"origen": "análisis", "sugerida": "internal", "decision": "ask_client"},       # la pasó al cliente
            {"origen": "análisis", "sugerida": "internal", "decision": "internal"},         # de acuerdo
            {"origen": "persona", "sugerida": "ask_client", "decision": "ask_client"},      # propia: no cuenta como acuerdo
            {"origen": "análisis", "sugerida": "ask_client"},                              # sin decidir: no cuenta
        ]
        c = dom.concordancia_de_preguntas(preguntas)
        self.assertEqual((c["decididas"], c["de_acuerdo"], c["sacadas_del_cliente"], c["pasadas_al_cliente"], c["propias"]),
                         (5, 2, 2, 1, 1))

    def test_no_questions_give_zeros(self):
        self.assertEqual(dom.concordancia_de_preguntas([])["decididas"], 0)
        self.assertEqual(dom.concordancia_de_preguntas(None)["propias"], 0)


class Resumen(unittest.TestCase):
    def caso(self, inicial, final, edito, preguntas=()):
        return {"cambios": dom.cambios_de_tabla(inicial, final), "tuvo_edicion": edito,
                "preguntas": dom.concordancia_de_preguntas(list(preguntas))}

    def test_the_group_summary(self):
        tabla = [fila("A", "10"), fila("B", "5")]
        sin_tocar = self.caso(tabla, tabla, False)
        corregido = self.caso(tabla, [fila("A", "12"), fila("C", "1")], True)
        r = dom.resumir_grupo([sin_tocar, corregido])
        self.assertEqual(r["casos"], 2)
        self.assertEqual(r["sin_correcciones"], 0.5)
        self.assertEqual(r["filas_conservadas"], 0.75)           # 3 de las 4 filas iniciales siguen
        self.assertEqual(r["cantidades_intactas"], 0.667)        # de las 3 conservadas, 2 sin cambiar la cantidad

    def test_few_cases_are_flagged_as_too_few_to_conclude(self):
        self.assertTrue(dom.resumir_grupo([self.caso([], [], False)])["pocos"])
        self.assertFalse(dom.resumir_grupo([self.caso([], [], False)] * dom.MINIMO_DE_CASOS)["pocos"])

    def test_with_nothing_to_measure_the_rates_are_empty(self):
        r = dom.resumir_grupo([])
        self.assertEqual(r["casos"], 0)
        self.assertIsNone(r["sin_correcciones"])
        self.assertIsNone(r["filas_conservadas"])


class Servicio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def caso(self, hilo, inicial, ediciones=(), prompt="PRECISO_V4.1", modelo="no indicado", preguntas=None, reanalisis=None):
        analisis = complete_analysis()
        analisis["activities"] = inicial
        analisis["work_skill_version"], analisis["work_model"] = prompt, modelo
        if preguntas:
            analisis["preguntas"] = preguntas
        db = store.open_db(Path(self.temp.name))
        store.save(db, hilo, "m-" + hilo, QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        for edicion in ediciones:
            store.add_table_version(db, hilo, edicion, "dashboard", "edición")
        if reanalisis is not None:
            store.add_table_version(db, hilo, reanalisis, "model", "reanálisis")
            store.add_table_version(db, hilo, [fila("Solo del reanálisis")], "dashboard", "edición posterior")
        db.commit()
        db.close()

    def test_cases_are_grouped_by_prompt_and_model(self):
        self.caso("a", [fila("A")], prompt="PRECISO_V4.1")
        self.caso("b", [fila("A")], prompt="PRECISO_V4.1")
        self.caso("c", [fila("A")], prompt="PRECISO_V4")
        self.caso("d", [fila("A")], prompt="", modelo="")
        grupos = {(g["prompt"], g["modelo"]): g["casos"] for g in servicio.por_grupo()}
        self.assertEqual(grupos[("PRECISO_V4.1", "no indicado")], 2)
        self.assertEqual(grupos[("PRECISO_V4", "no indicado")], 1)
        self.assertEqual(grupos[("sin dato", "sin dato")], 1)

    def test_a_case_without_edits_counts_as_untouched_and_one_with_edits_shows_its_changes(self):
        self.caso("a", [fila("A", "10")])
        self.caso("b", [fila("A", "10"), fila("B", "5")], ediciones=[[fila("A", "12"), fila("B", "5")]])
        g = servicio.por_grupo()[0]
        self.assertEqual(g["casos"], 2)
        self.assertEqual(g["resumen"]["sin_correcciones"], 0.5)
        self.assertEqual(g["resumen"]["cantidades_intactas"], 0.667)     # de las 3 filas conservadas, 1 cambió de cantidad

    def test_changes_after_a_reanalysis_are_not_charged_to_the_first_analysis(self):
        self.caso("a", [fila("A", "10")], ediciones=[[fila("A", "10")]], reanalisis=[fila("A", "10")])
        resumen = servicio.por_grupo()[0]["resumen"]
        self.assertEqual(resumen["filas_conservadas"], 1.0)               # la fila rara posterior al reanálisis no cuenta

    def test_a_case_that_has_no_analysis_version_is_left_out(self):
        db = store.open_db(Path(self.temp.name))
        store.save(db, "x", "m-x", QuoteStatus.IGNORED, "cliente@empresa.co", complete_analysis())
        db.execute("DELETE FROM table_versions")
        db.commit()
        db.close()
        self.assertEqual(servicio.por_grupo(), [])

    def test_the_questions_agreement_comes_from_the_saved_decisions(self):
        self.caso("a", [fila("A")], preguntas=[
            {"id": "q1", "texto": "¿Datum?", "origen": "análisis", "sugerida": "ask_client", "decision": "ask_client"},
            {"id": "q2", "texto": "KML", "origen": "análisis", "sugerida": "ask_client", "decision": "internal"}])
        p = servicio.por_grupo()[0]["preguntas"]
        self.assertEqual((p["decididas"], p["de_acuerdo"], p["sacadas_del_cliente"]), (2, 1, 1))


class Pantalla(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def test_the_page_says_there_is_nothing_to_measure_yet(self):
        pagina = paginas.render_desempeno()
        self.assertIn("Desempeño", pagina)
        self.assertIn("Todavía no hay casos", pagina)

    def test_the_page_shows_each_group_with_its_rates_and_the_caution(self):
        analisis = complete_analysis()
        analisis["activities"] = [fila("A", "10")]
        analisis["work_skill_version"], analisis["work_model"] = "PRECISO_V4.1", "no indicado"
        db = store.open_db(Path(self.temp.name))
        store.save(db, "a", "m-a", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.close()
        pagina = paginas.render_desempeno()
        self.assertIn("PRECISO_V4.1", pagina)
        self.assertIn("pocos casos", pagina.lower())
        self.assertIn("nadie revisó", pagina)

    def test_the_route_is_registered_and_documented(self):
        self.assertIn("/desempeno", web.GET_ROUTES)
        self.assertIn("/desempeno", openapi.rutas_documentadas()[0])


if __name__ == "__main__":
    unittest.main()
