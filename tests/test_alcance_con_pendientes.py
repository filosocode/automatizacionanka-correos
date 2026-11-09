"""Con actividades sin confirmar no se salta a la T (U-87).

Cubre: U-87
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio import decision as decisiones
from anka.dominio.reception import actividades_sin_confirmar, preguntas_de_pendientes
from anka.dominio.status import QuoteStatus
from tests.test_app import complete_analysis, test_config


def con_pendiente(**extra):
    analisis = complete_analysis()
    analisis["activities"].append({
        "activity": "Control vertical", "ubicacion": "La Calera", "servicio_tecnica": "Nivelación",
        "quantity": "no indicado", "unit": "", "entregable": "Carteras", "plazo_restriccion": "no indicado",
        "fuente": "pliego.pdf, Página 3", "estado": "pendiente",
        "notes": "Falta definir la longitud total del trazado a nivelar. Fuente revisada: pliego.pdf. "
                 "Siguiente paso: esperar el archivo del cliente.",
    })
    return analisis | extra


def decidir(analisis, **banderas):
    return decisiones.decide_analysis(
        "t-1", analisis, [], "cliente@example.com", False, test_config(),
        review_only=banderas.get("review_only", True), gmail_drafts=False,
    )


# Cubre: U-87
class SinConfirmarNoSaltaALaT(unittest.TestCase):
    def test_todo_confirmado_sigue_siendo_alcance_completo(self):
        self.assertEqual(decidir(complete_analysis()).status, QuoteStatus.READY_REVIEW)

    def test_una_actividad_pendiente_pide_datos_en_vez_de_ofrecer_la_t(self):
        decision = decidir(con_pendiente())
        self.assertEqual(decision.status, QuoteStatus.REVIEW_PENDING)
        self.assertEqual(decision.action, decisiones.Action.QUEUE)
        self.assertTrue(any("Control vertical" in q and "longitud total del trazado" in q
                            for q in decision.questions), decision.questions)
        self.assertEqual(decision.folder_name, "")

    def test_una_actividad_contradictoria_tampoco_pasa(self):
        analisis = complete_analysis()
        analisis["activities"][0]["estado"] = "contradictorio"
        self.assertEqual(decidir(analisis).status, QuoteStatus.REVIEW_PENDING)

    def test_el_estado_sin_tildes_o_con_matices_cuenta_como_pendiente(self):
        analisis = complete_analysis()
        analisis["activities"][0]["estado"] = "confirmado; soporte pendiente"
        self.assertEqual(decidir(analisis).status, QuoteStatus.REVIEW_PENDING)

    def test_las_dudas_del_analisis_van_primero_en_las_preguntas(self):
        analisis = con_pendiente(other_questions=["¿Cuándo enviarán el archivo del área?"])
        preguntas = preguntas_de_pendientes(analisis)
        self.assertEqual(preguntas[0], "¿Cuándo enviarán el archivo del área?")
        self.assertEqual(len(preguntas), len(set(preguntas)))

    def test_sin_nota_de_faltante_se_hace_una_pregunta_generica(self):
        analisis = complete_analysis()
        analisis["activities"][0].update(estado="pendiente", notes="")
        (pregunta,) = preguntas_de_pendientes(analisis)
        self.assertIn("Levantamiento topográfico", pregunta)
        self.assertIn("cantidad, ubicación o plazo", pregunta)

    def test_si_el_comercial_ya_decidio_se_respeta(self):
        decision = decidir(con_pendiente(manual_review=True))
        self.assertEqual(decision.status, QuoteStatus.READY_REVIEW)

    def test_las_actividades_sin_confirmar_se_listan(self):
        self.assertEqual([a["activity"] for a in actividades_sin_confirmar(con_pendiente())], ["Control vertical"])
        self.assertEqual(actividades_sin_confirmar(complete_analysis()), [])


if __name__ == "__main__":
    unittest.main()
