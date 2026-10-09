"""Una magnitud solo cuenta como conocida si trae una cantidad. Cubre: F1-04"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.reception import reception_assessment


def analisis(cantidad, unidad="No indicado", estado="pendiente", campos=()):
    return {"is_quote_request": True, "project_name": "Predio en Soacha", "summary": "Topografía de un predio.",
            "fields": [{"name": nombre, "value": valor} for nombre, valor in campos],
            "activities": [{"activity": "levantamiento topográfico", "quantity": cantidad, "unit": unidad,
                            "estado": estado, "ubicacion": "Soacha, Cundinamarca"}]}


def pregunta_de_magnitud(evaluacion):
    return any("área" in q.lower() or "longitud" in q.lower() for q in evaluacion["questions"])


class MagnitudConocida(unittest.TestCase):
    def test_a_label_in_the_quantity_field_is_not_a_measure(self):
        # el defecto real: el modelo escribió «Área» como cantidad y el caso salió «información suficiente»
        for texto in ("Área", "área", "Longitud", "Extensión del predio", "varios", "grande", "-", "por definir"):
            with self.subTest(cantidad=texto):
                evaluacion = reception_assessment(analisis(texto))
                self.assertTrue(pregunta_de_magnitud(evaluacion), evaluacion["questions"])
                self.assertNotIn("suficiente", evaluacion["label"].lower())

    def test_a_real_quantity_is_a_measure(self):
        for texto in ("5 hectáreas", "544,5 ha", "1.200 m", "2 km²", "10 puntos", "100", "una hectárea", "Cinco km"):
            with self.subTest(cantidad=texto):
                self.assertFalse(pregunta_de_magnitud(reception_assessment(analisis(texto, "ha", "confirmado"))))

    def test_a_field_without_a_number_is_not_a_measure_either(self):
        evaluacion = reception_assessment(analisis("No indicado", campos=[("Área o longitud", "el lote completo")]))
        self.assertTrue(pregunta_de_magnitud(evaluacion))
        evaluacion = reception_assessment(analisis("No indicado", campos=[("Área o longitud", "12 hectáreas")]))
        self.assertFalse(pregunta_de_magnitud(evaluacion))


if __name__ == "__main__":
    unittest.main()
