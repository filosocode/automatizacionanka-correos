"""La evaluación con solicitudes reales del histórico: comparar, resumir, muestrear y preparar fichas. Cubre: T-34"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import evaluacion as ev
from anka.infra import settings
from anka.servicios import evaluacion as servicio


def analisis(actividades=(), solicitud=True, resumen="", proyecto=""):
    return {"is_quote_request": solicitud, "project_name": proyecto, "summary": resumen, "fields": [],
            "activities": [dict(a) for a in actividades]}


def esperado(**extra):
    base = {"es_solicitud": True, "ubicacion": ["Villavicencio", "Meta"],
            "actividades": [{"servicio": "levantamiento lidar", "cantidad": 100, "unidad": "ha"}],
            "faltante": ["plazo"]}
    base.update(extra)
    return base


class Numeros(unittest.TestCase):
    def test_quantities_are_read_in_the_ways_clients_write_them(self):
        casos = {"544,5 ha": 544.5, "1.200 m": 1200.0, "5 hectáreas": 5.0, "2 km²": 2.0, "12.5": 12.5, "100": 100.0,
                 "10,000 m": 10000.0, "sin dato": None, "": None, None: None}
        for texto, valor in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(ev.numero(texto), valor)


class Preguntas(unittest.TestCase):
    def test_the_topic_of_each_question_is_recognised(self):
        preguntas = ["¿Puede indicar la ubicación del proyecto?", "¿Cuál es el área, longitud o cantidad aproximada?",
                     "¿Cuál es el plazo de entrega esperado?", "¿Qué formato de entregables necesitan?"]
        self.assertEqual(ev.temas_preguntados(preguntas), {"ubicacion", "magnitud", "plazo", "entregables"})

    def test_a_question_with_no_known_topic_is_not_counted_as_any(self):
        self.assertEqual(ev.temas_preguntados(["¿Cómo se llama el encargado?"]), set())


class Ausentes(unittest.TestCase):
    def test_the_topics_a_request_does_not_mention_are_what_should_have_been_asked(self):
        solicitud = "Necesitamos un levantamiento LiDAR en el municipio de Sahagún, Córdoba, de 120 hectáreas."
        self.assertEqual(ev.temas_en(solicitud), {"ubicacion", "magnitud"})
        self.assertNotIn("ubicacion", ev.temas_ausentes(solicitud))
        self.assertNotIn("magnitud", ev.temas_ausentes(solicitud))
        self.assertIn("plazo", ev.temas_ausentes(solicitud))

    def test_a_named_place_or_a_figure_with_a_unit_counts_even_without_the_words(self):
        self.assertEqual(ev.temas_en("Levantamiento en Bogotá de 5 hectáreas.", ["Bogotá"]), {"ubicacion", "magnitud"})
        self.assertNotIn("ubicacion", ev.temas_en("Levantamiento de 5 hectáreas."))          # sin el lugar, no se sabe dónde
        self.assertIn("magnitud", ev.temas_en("Son 3,5 km de vía."))
        self.assertNotIn("magnitud", ev.temas_en("Son 3 propuestas."))                         # una cifra sin unidad no es magnitud

    def test_a_request_that_says_nothing_lacks_every_topic(self):
        self.assertEqual(ev.temas_ausentes("Hola."), sorted(ev.TEMAS))

    def test_the_draft_ficha_takes_what_is_missing_from_the_request_text_not_from_the_model(self):
        from anka.servicios import evaluacion as servicio_
        with patch.object(servicio_, "_texto_de", side_effect=["Plazo de 30 días en Bogotá, 5 hectáreas.", "propuesta"]), \
                patch("anka.infra.llm.generar_json", return_value={"ubicacion": ["Bogotá"], "actividades": [],
                                                                    "faltante": ["plazo"]}):
            ficha = servicio_.preparar_ficha({"ruta": "I:/x", "solicitud": ["a.pdf"], "propuesta": ["b.pdf"],
                                              "servicio": "topografia"}, "c01", {"provider": "ollama"})
        self.assertNotIn("plazo", ficha["esperado"]["faltante"])
        self.assertNotIn("ubicacion", ficha["esperado"]["faltante"])
        self.assertIn("objetivo", ficha["esperado"]["faltante"])
        self.assertEqual(ficha["estado"], servicio_.BORRADOR)


class Comparar(unittest.TestCase):
    def test_a_perfect_analysis_scores_everything(self):
        obtenido = analisis([{"activity": "Levantamiento LiDAR aerotransportado", "quantity": "100", "unit": "ha"}],
                            proyecto="Parque solar", resumen="En Villavicencio, Meta.")
        r = ev.comparar(esperado(), obtenido, ["¿Cuál es el plazo de entrega?"])
        self.assertTrue(r["identifica"])
        self.assertEqual((r["actividades_esperadas"], r["actividades_encontradas"], r["actividades_obtenidas"]), (1, 1, 1))
        self.assertEqual((r["cantidades_esperadas"], r["cantidades_correctas"]), (1, 1))
        self.assertEqual(r["ubicacion"], 1.0)
        self.assertEqual((r["preguntas_acertadas"], r["preguntas_de_mas"], r["preguntas_omitidas"]), (1, 0, 0))

    def test_a_wrong_quantity_is_found_but_not_correct(self):
        obtenido = analisis([{"activity": "Levantamiento LiDAR", "quantity": "10", "unit": "ha"}])
        r = ev.comparar(esperado(), obtenido, [])
        self.assertEqual((r["actividades_encontradas"], r["cantidades_correctas"]), (1, 0))

    def test_the_unit_must_match_after_normalising_it(self):
        bien = ev.comparar(esperado(), analisis([{"activity": "LiDAR", "quantity": "100", "unit": "Hectáreas"}]), [])
        mal = ev.comparar(esperado(), analisis([{"activity": "LiDAR", "quantity": "100", "unit": "km"}]), [])
        self.assertEqual(bien["cantidades_correctas"], 1)
        self.assertEqual(mal["cantidades_correctas"], 0)

    def test_an_extra_activity_lowers_precision_not_recall(self):
        obtenido = analisis([{"activity": "Levantamiento LiDAR", "quantity": "100", "unit": "ha"},
                             {"activity": "Batimetría", "quantity": "3", "unit": "km"}])
        r = ev.comparar(esperado(), obtenido, [])
        self.assertEqual((r["actividades_encontradas"], r["actividades_obtenidas"]), (1, 2))

    def test_an_activity_is_matched_only_once(self):
        obtenido = analisis([{"activity": "LiDAR", "quantity": "100", "unit": "ha"},
                             {"activity": "LiDAR", "quantity": "100", "unit": "ha"}])
        r = ev.comparar(esperado(), obtenido, [])
        self.assertEqual(r["actividades_encontradas"], 1)

    def test_a_missing_activity_is_not_found(self):
        r = ev.comparar(esperado(), analisis([]), [])
        self.assertEqual((r["actividades_esperadas"], r["actividades_encontradas"]), (1, 0))

    def test_not_recognising_the_request_is_a_miss(self):
        self.assertFalse(ev.comparar(esperado(), analisis(solicitud=False), [])["identifica"])
        self.assertTrue(ev.comparar(esperado(es_solicitud=False), analisis(solicitud=False), [])["identifica"])

    def test_the_location_counts_by_fraction_of_the_expected_places_found(self):
        r = ev.comparar(esperado(), analisis(resumen="Proyecto en Villavicencio."), [])
        self.assertEqual(r["ubicacion"], 0.5)
        sin = ev.comparar(esperado(ubicacion=[]), analisis(), [])
        self.assertIsNone(sin["ubicacion"])                      # no se evalúa lo que la ficha no esperaba

    def test_the_location_ignores_case_and_accents(self):
        self.assertEqual(ev.comparar(esperado(ubicacion=["Bogotá"]), analisis(resumen="en BOGOTA"), [])["ubicacion"], 1.0)

    def test_asking_what_was_already_in_the_request_is_counted_and_so_is_not_asking_what_was_missing(self):
        r = ev.comparar(esperado(faltante=["plazo", "entregables"]), analisis(),
                        ["¿Cuál es el plazo?", "¿Cuál es el área del predio?"])
        self.assertEqual((r["preguntas_acertadas"], r["preguntas_de_mas"], r["preguntas_omitidas"]), (1, 1, 1))


class Resumir(unittest.TestCase):
    def test_the_summary_adds_up_cases_and_leaves_out_what_was_not_evaluated(self):
        a = ev.comparar(esperado(), analisis([{"activity": "LiDAR", "quantity": "100", "unit": "ha"}],
                                             resumen="Villavicencio Meta"), ["¿Plazo?"])
        b = ev.comparar(esperado(ubicacion=[]), analisis([]), [])
        r = ev.resumir([a, b])
        self.assertEqual(r["casos"], 2)
        self.assertEqual(r["identificacion"], 1.0)
        self.assertEqual(r["actividades_recall"], 0.5)
        self.assertEqual(r["actividades_precision"], 1.0)
        self.assertEqual(r["cantidades"], 0.5)
        self.assertEqual(r["ubicacion"], 1.0)                    # solo el caso que la esperaba

    def test_with_no_cases_there_is_no_accuracy_to_report(self):
        r = ev.resumir([])
        self.assertEqual(r["casos"], 0)
        self.assertTrue(all(valor is None for clave, valor in r.items() if clave != "casos"))


class Muestreo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = Path(self.temp.name)

    def archivo(self, *partes):
        ruta = self.raiz.joinpath(*partes)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(b"x")

    def test_only_folders_with_a_request_and_a_proposal_are_candidates(self):
        self.archivo("ClienteA", "P1", "Solicitud de cotizacion LiDAR.pdf")
        self.archivo("ClienteA", "P1", "Propuesta T&E 25001.pdf")
        self.archivo("ClienteB", "P2", "Solicitud topografia.pdf")           # sin propuesta
        self.archivo("ClienteC", "P3", "Propuesta final.pdf")                 # sin solicitud
        self.archivo("ClienteD", "P4", "Solicitud x.dwg")                     # formato que no se lee
        self.archivo("ClienteD", "P4", "Propuesta x.pdf")
        encontradas = servicio.candidatas(self.raiz)
        self.assertEqual([Path(c["ruta"]).name for c in encontradas], ["P1"])
        self.assertEqual(encontradas[0]["cliente"], "ClienteA")

    def test_the_sample_takes_one_case_per_client_and_spreads_over_services(self):
        for cliente, servicio_ in [("C1", "LiDAR"), ("C2", "LiDAR"), ("C3", "batimetria"), ("C4", "topografia"),
                                   ("C5", "topografia"), ("C6", "fotogrametria")]:
            for proyecto in ("P1", "P2"):
                self.archivo(cliente, proyecto, f"Solicitud {servicio_}.pdf")
                self.archivo(cliente, proyecto, "Propuesta final.pdf")
        muestra = servicio.muestrear(servicio.candidatas(self.raiz), 4, semilla=3)
        self.assertEqual(len(muestra), 4)
        self.assertEqual(len({c["cliente"] for c in muestra}), 4)             # ningún cliente repetido
        self.assertGreaterEqual(len({c["servicio"] for c in muestra}), 3)     # reparte entre servicios

    def test_the_same_seed_gives_the_same_sample(self):
        for n in range(8):
            self.archivo(f"C{n}", "P", "Solicitud topografia.pdf")
            self.archivo(f"C{n}", "P", "Propuesta.pdf")
        c = servicio.candidatas(self.raiz)
        self.assertEqual(servicio.muestrear(c, 3, semilla=1), servicio.muestrear(c, 3, semilla=1))

    def test_asking_for_more_than_there_are_returns_what_there_is(self):
        self.archivo("C1", "P", "Solicitud topografia.pdf")
        self.archivo("C1", "P", "Propuesta.pdf")
        self.assertEqual(len(servicio.muestrear(servicio.candidatas(self.raiz), 10, semilla=1)), 1)


class Fichas(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def ficha(self, **extra):
        base = {"id": "c01", "estado": servicio.BORRADOR, "origen": "I:/T&E/Cliente/P1", "servicio": "topografia",
                "correo": "Buen día, adjunto la solicitud.", "adjuntos": [], "esperado": esperado()}
        base.update(extra)
        return base

    def test_a_ficha_is_saved_in_the_team_data_folder_not_in_the_repository(self):
        ruta = servicio.guardar_ficha(self.ficha())
        self.assertEqual(Path(self.temp.name) in ruta.parents, True)
        self.assertEqual(json.loads(ruta.read_text(encoding="utf-8"))["id"], "c01")

    def test_only_validated_fichas_are_evaluated(self):
        servicio.guardar_ficha(self.ficha(id="c01"))
        servicio.guardar_ficha(self.ficha(id="c02", estado=servicio.VALIDADA))
        self.assertEqual([f["id"] for f in servicio.fichas_validadas()], ["c02"])

    def test_validating_a_ficha_needs_an_expected_result(self):
        servicio.guardar_ficha(self.ficha(esperado={}))
        with self.assertRaises(ValueError):
            servicio.validar("c01")
        servicio.guardar_ficha(self.ficha())
        self.assertEqual(servicio.validar("c01")["estado"], servicio.VALIDADA)

    def test_without_validated_fichas_no_accuracy_is_computed(self):
        servicio.guardar_ficha(self.ficha())
        with self.assertRaisesRegex(ValueError, "validad"):
            servicio.evaluar(analizar=lambda ficha: ({}, []))

    def test_the_report_records_model_prompt_date_and_each_case(self):
        servicio.guardar_ficha(self.ficha(estado=servicio.VALIDADA))
        informe = servicio.evaluar(
            analizar=lambda ficha: (analisis([{"activity": "LiDAR", "quantity": "100", "unit": "ha"}],
                                             resumen="Villavicencio Meta"), ["¿Plazo?"]),
            modelo="modelo-de-prueba", version_del_prompt="PRECISO_V4.1")
        self.assertEqual((informe["modelo"], informe["version_del_prompt"]), ("modelo-de-prueba", "PRECISO_V4.1"))
        self.assertTrue(informe["fecha"])
        self.assertEqual(informe["resumen"]["casos"], 1)
        self.assertEqual(informe["casos"][0]["id"], "c01")
        self.assertTrue(Path(informe["archivo"]).exists())

    def test_a_case_that_fails_is_reported_and_does_not_stop_the_others(self):
        servicio.guardar_ficha(self.ficha(id="c01", estado=servicio.VALIDADA))
        servicio.guardar_ficha(self.ficha(id="c02", estado=servicio.VALIDADA))

        def analizar(ficha):
            if ficha["id"] == "c01":
                raise RuntimeError("el modelo no respondió")
            return analisis([]), []

        informe = servicio.evaluar(analizar=analizar)
        self.assertEqual(informe["resumen"]["casos"], 1)
        self.assertEqual([f["id"] for f in informe["fallidos"]], ["c01"])


if __name__ == "__main__":
    unittest.main()
