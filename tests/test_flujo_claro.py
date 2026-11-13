"""Revisión progresiva y preguntas coherentes tras una respuesta parcial. Cubre: T-134"""
from tests import _isolate  # noqa: F401

from anka.dominio.preguntas import candidatas, pregunta_ya_resuelta
from anka.web import pantalla_caso
from tests.test_suficiencia_documental import Base, analisis_valido


def parcial():
    a = analisis_valido()
    a["summary"] = "BORRADOR INTERNO — NO ENVIAR. Mensaje SENT abc123."
    a["question_audit"] = [
        {"question": "Intervalo de curvas", "disposition": "do_not_ask"},
        {"question": "Vigencia", "disposition": "internal_review"},
        {"question": "Fecha límite", "disposition": "ask_client"}]
    a["other_questions"] = ["Vigencia (critical)", "Fecha límite (critical)"]
    a["rondas_de_preguntas"] = [{"en": "2026-11-03T10:00:00-05:00", "mensaje": "abc123",
        "respuesta": {"en": "2026-11-03T11:00:00-05:00", "mensaje": "def456"}, "preguntas": [
            {"texto": "¿Qué intervalo de curvas necesitan?", "estado": "resuelta",
             "conciliacion": {"mensaje": "def456"}},
            {"texto": "¿Cuál es la fecha límite?", "estado": "sigue_abierta"}]}]
    return a


class FlujoClaro(Base):
    def test_resuelta_se_excluye_sin_borrar_historial_y_no_duplica_internas(self):
        a = parcial()
        self.assertTrue(pregunta_ya_resuelta("Intervalo de curvas", a))
        candidatas_ = candidatas(a)
        self.assertEqual([p["texto"] for p in candidatas_], ["Vigencia", "Fecha límite"])
        self.assertEqual(a["rondas_de_preguntas"][0]["preguntas"][0]["estado"], "resuelta")
        a["ultimas_preguntas"] = ["¿Qué intervalo de curvas necesitan?", "¿Cuál es la fecha límite?"]
        self.assertEqual(pantalla_caso._cuadro_de_preguntas(a, []), "¿Cuál es la fecha límite?")
        a["rondas_de_preguntas"][0]["preguntas"][0]["conciliacion"] = {}
        self.assertFalse(pregunta_ya_resuelta("Intervalo de curvas", a))

    def test_orden_de_revision_y_correo_anterior_no_listo_para_reenviar(self):
        a = parcial()
        self.guardar("claro", a)
        html = pantalla_caso.render_case("claro")
        self.assertNotIn('id="guia-caso-titulo"', html)       # U-103.10: la tarjeta «Resumen de la solicitud» se retiró
        self.assertLess(html.index('id="alcance"'), html.index('id="decision"'))   # U-107: actividades y mensaje, en ese orden
        self.assertNotIn('id="cuerpo-correo"', html)
        self.assertNotIn('<strong>Información validada.</strong>', html)
        self.assertIn("Pendiente del cliente: ¿Cuál es la fecha límite?", html)
        self.assertIn("Ver análisis original", html)

    def test_sin_respuesta_no_inventa_resoluciones(self):
        a = parcial()
        a["rondas_de_preguntas"][0]["respuesta"] = None
        self.assertFalse(pregunta_ya_resuelta("Intervalo de curvas", a))
