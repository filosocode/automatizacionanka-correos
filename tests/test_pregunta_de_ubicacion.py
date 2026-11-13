"""Sin archivo de ubicación, siempre se pide (U-95).

Cubre: U-95
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.reception import PREGUNTA_DE_UBICACION, agregar_pregunta_de_ubicacion


def analisis(*cantidades, preguntas=None, solicitud=True):
    return {"is_quote_request": solicitud, "other_questions": list(preguntas or []),
            "activities": [{"activity": f"A{n}", "quantity": c} for n, c in enumerate(cantidades)]}


PDF = {"filename": "Especificaciones.pdf", "status": "extraido"}


# Cubre: U-95
class PreguntaDeUbicacion(unittest.TestCase):
    def test_sin_cantidad_y_sin_archivo_geografico_se_pregunta(self):
        a = analisis("5", "no indicado")
        self.assertTrue(agregar_pregunta_de_ubicacion(a, [PDF]))
        self.assertEqual(a["other_questions"], [PREGUNTA_DE_UBICACION])

    def test_si_el_modelo_ya_la_hizo_no_se_repite(self):
        a = analisis("no indicado", preguntas=["¿Pueden enviar el KMZ o el polígono del proyecto?"])
        self.assertFalse(agregar_pregunta_de_ubicacion(a, [PDF]))
        self.assertEqual(len(a["other_questions"]), 1)

    def test_con_un_archivo_geografico_no_se_pregunta(self):
        for nombre in ("Calle153.KMZ", "zona.kml", "lote.shp", "plano.dwg", "paquete.zip"):
            with self.subTest(nombre=nombre):
                a = analisis("no indicado")
                self.assertFalse(agregar_pregunta_de_ubicacion(a, [PDF, {"filename": nombre}]))
                self.assertEqual(a["other_questions"], [])

    def test_si_todas_las_actividades_traen_cantidad_no_se_pregunta(self):
        a = analisis("10", "300")
        self.assertFalse(agregar_pregunta_de_ubicacion(a, [PDF]))

    def test_no_pregunta_si_no_es_una_solicitud_o_no_hay_actividades(self):
        self.assertFalse(agregar_pregunta_de_ubicacion(analisis("no indicado", solicitud=False), [PDF]))
        self.assertFalse(agregar_pregunta_de_ubicacion(analisis(), [PDF]))

    def test_una_pregunta_distinta_del_modelo_no_impide_agregarla(self):
        a = analisis("no indicado", preguntas=["¿Podrían remitir el Anexo Capitulo_1_Topografia?"])
        self.assertTrue(agregar_pregunta_de_ubicacion(a, [PDF]))
        self.assertEqual(len(a["other_questions"]), 2)


if __name__ == "__main__":
    unittest.main()
