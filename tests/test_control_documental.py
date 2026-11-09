"""Control de lectura con evidencia adversa y bloqueo antes de suficiencia. Cubre: T-133"""
from tests import _isolate  # noqa: F401

import unittest
from pathlib import Path

from anka.dominio.work_contract import control_documental, motivo_de_insuficiencia_documental
from anka.dominio.reception import unresolved_scope_issues
from anka.infra.attachments import extract_attachment_text
from anka.dominio.errores import ErrorDeValidacion
from anka.servicios import quotes
from anka.servicios.change_control import assign_code
from tests.test_suficiencia_documental import Base, analisis_valido

ROOT = Path(__file__).resolve().parents[1]


class Control(unittest.TestCase):
    def test_contradiccion_no_se_resuelve_con_revision_personal(self):
        a = {"contradictions": [{"details": "7 predios frente a 6", "status": "pendiente"}],
             "reading_review": {"at": "hoy"}}
        self.assertEqual(control_documental(a)["estado"], "Datos contradictorios")
        self.assertIn("7 predios", motivo_de_insuficiencia_documental(a))
        a["contradictions"][0]["status"] = "resuelta"
        self.assertFalse(control_documental(a)["bloquea"])

    def test_visual_y_parcial_requieren_revision(self):
        a = {"attachments": [{"filename": "plano.pdf", "coverage": "parcial",
                              "visual_interpretation": "no_certificada"}]}
        self.assertEqual(control_documental(a)["estado"], "Requiere revisión visual")
        self.assertTrue(unresolved_scope_issues(a))
        a["reading_review"] = {"at": "hoy"}
        self.assertFalse(control_documental(a)["bloquea"])

    def test_sin_evidencia_no_se_declara_completa(self):
        self.assertEqual(control_documental({})["estado"], "Lectura sin verificar")
        a = {"attachments": [{"filename": "logo.png", "status": "ignorado", "coverage": "parcial"}]}
        self.assertFalse(control_documental(a)["bloquea"])

    def test_kmz_advierte_area_discrepante_con_fuente(self):
        p = ROOT / "simulaciones/adjuntos/01-solar-lidar/predio_candelaria_82ha.kmz"
        if not p.exists():
            self.skipTest("Fixture local no distribuido")
        texto, aviso = extract_attachment_text(p.name, "application/vnd.google-earth.kmz", p.read_bytes())
        self.assertIn("82 ha", texto)
        self.assertIn("75.7405", texto)
        self.assertIn("VALIDACIÓN PENDIENTE: elemento «Predio Candelaria»", texto)
        self.assertIn("validación documental pendiente", aviso)

    def test_excel_detecta_siete_frente_a_seis_y_vigencia(self):
        p = ROOT / "laboratorio/banco_documental/documentos/cantidades_03.xlsx"
        texto, aviso = extract_attachment_text(p.name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", p.read_bytes())
        self.assertIn("Resumen!C9 declara 7", texto)
        self.assertIn("Detalle!A1 contiene 6", texto)
        self.assertIn("hoja oculta: confirmar vigencia", texto)
        self.assertIn("valor guardado: 33.7", texto)
        self.assertIn("validación documental pendiente", aviso)


class BloqueoServidor(Base):
    def test_no_confirma_conflicto_ni_cambia_estado(self):
        a = analisis_valido()
        a["contradictions"] = [{"details": "7 frente a 6", "status": "pendiente"}]
        self.guardar("doc", a)
        with self.assertRaisesRegex(ErrorDeValidacion, "7 frente a 6"):
            quotes.resolve_technical_review("doc", "sufficient")
        with self.assertRaisesRegex(ErrorDeValidacion, "7 frente a 6"):
            assign_code("doc", "26998")
        self.assertEqual(self.caso("doc")["status"], "review")

    def test_visual_se_puede_revisar_explicitamente(self):
        a = analisis_valido()
        a["attachments"][0].update(coverage="parcial", visual_interpretation="no_certificada")
        self.guardar("doc", a)
        with self.assertRaises(ErrorDeValidacion):
            quotes.resolve_technical_review("doc", "sufficient")
        quotes.aceptar_lectura_revisada("doc")
        quotes.resolve_technical_review("doc", "sufficient")
        self.assertEqual(self.caso("doc")["status"], "ready_review")
