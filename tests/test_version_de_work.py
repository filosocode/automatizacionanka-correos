"""Un borrador de V6 que omite skill_version se verifica igual contra Gmail. Cubre: T-44"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.dominio.status import QuoteStatus
from anka.dominio.work_contract import usa_verificacion_por_adjuntos
from anka.infra import settings
from anka.servicios import quotes, work_import
from anka.web import pantalla_caso
from tests.test_lectura_de_adjuntos import (
    NOTAS, PLANO, PROPIAS, GmailConAdjuntos, mensaje, payload, reportado,
)
from tests.test_puente_determinista import draft_message

PROMPT = Path(__file__).resolve().parents[1] / "docs" / "PROMPT_WORK_ANKA_V6.txt"
CON_TAMANO = [{"filename": "a.pdf", "size_bytes": 10}, {"filename": "b.dwg", "size_bytes": 20}]


class LaHuellaDeV6(unittest.TestCase):
    def test_la_version_declarada_manda(self) -> None:
        # Cubre: T-44
        for version, esperado in (("PRECISO_V6.1.1", True), ("PRECISO_V7.0", True), ("PRECISO_V5.0", False),
                                  ("PRECISO_V4.5_DRIVE", False)):
            # aunque los adjuntos traigan size_bytes, una versión declarada de V5 no se reinterpreta
            self.assertIs(usa_verificacion_por_adjuntos({"skill_version": version, "attachments": CON_TAMANO}),
                          esperado, version)

    def test_sin_version_decide_que_todos_los_adjuntos_traigan_su_tamano_exacto(self) -> None:
        # Cubre: T-44
        for version in (None, "", "no indicado", "cualquier cosa"):
            self.assertTrue(usa_verificacion_por_adjuntos({"skill_version": version, "attachments": CON_TAMANO}), version)
        self.assertTrue(usa_verificacion_por_adjuntos({"attachments": CON_TAMANO}))               # campo ausente

    def test_sin_la_huella_completa_sigue_el_camino_de_siempre(self) -> None:
        # Cubre: T-44
        casos = (
            {"attachments": []},                                                       # sin adjuntos no hay huella
            {},
            {"attachments": [{"filename": "a.pdf"}]},                                  # V5: sin size_bytes
            {"attachments": [CON_TAMANO[0], {"filename": "b.dwg"}]},                   # uno sin tamaño
            {"attachments": [{"filename": "a.pdf", "size_bytes": "10"}]},              # no es entero
            {"attachments": [{"filename": "a.pdf", "size_bytes": True}]},              # un booleano no es un tamaño
            {"attachments": "a.pdf"},
            {"attachments": [None]},
        )
        for datos in casos:
            self.assertFalse(usa_verificacion_por_adjuntos(datos), datos)

    def test_lo_que_no_es_un_borrador_no_se_verifica(self) -> None:
        # Cubre: T-44
        self.assertFalse(usa_verificacion_por_adjuntos(None))     # type: ignore[arg-type]
        self.assertFalse(usa_verificacion_por_adjuntos([CON_TAMANO]))  # type: ignore[arg-type]


class ElPuenteConUnBorradorSinVersion(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"

    def importar(self, datos: dict) -> dict:
        gmail = GmailConAdjuntos({"d1": draft_message(datos)}, {})
        gmail.hilos["T1"] = [mensaje("m1", [NOTAS, PLANO])]
        sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, {}, PROPIAS)
        return quotes.get_quote("T1")["analysis"]

    def borrador(self, **extra: object) -> dict:
        return payload(analysis_complete=True, attachments=[reportado(NOTAS[0], NOTAS[1]),
                                                           reportado(PLANO[0], PLANO[1])], **extra)

    def test_el_caso_real_un_v6_sin_version_ya_no_pide_el_manifiesto_de_drive(self) -> None:
        # Cubre: T-44
        datos = self.borrador()
        self.assertNotIn("skill_version", datos)
        analisis = self.importar(datos)
        self.assertEqual(analisis["reading_audit"]["verification"], "verificada")
        self.assertFalse(pantalla_caso._reading_is_blocked(analisis))
        self.assertEqual(analisis.get("DRIVE_PREFLIGHT"), {})                       # no se pidió ningún manifiesto
        self.assertNotIn("manifiesto y los segmentos", pantalla_caso._texto_de_lectura_pendiente(analisis))
        self.assertNotEqual(quotes.get_quote("T1")["status"], QuoteStatus.REVIEW)       # la lectura no lo manda a revisión

    def test_sin_version_y_con_un_adjunto_que_falta_se_nombra_lo_que_falta(self) -> None:
        # Cubre: T-44
        analisis = self.importar(payload(analysis_complete=True, attachments=[reportado(NOTAS[0], NOTAS[1])]))
        self.assertEqual(analisis["reading_audit"]["attachment_files_pending"], ["plano.txt"])
        self.assertTrue(pantalla_caso._reading_is_blocked(analisis))
        self.assertIn("Falta leer: plano.txt.", pantalla_caso._texto_de_lectura_pendiente(analisis))

    def test_una_version_declarada_de_v5_sigue_con_el_manifiesto(self) -> None:
        # Cubre: T-44
        analisis = self.importar(self.borrador(skill_version="PRECISO_V5.0"))
        self.assertNotIn("verification", analisis.get("reading_audit") or {})


class ElPrompt(unittest.TestCase):
    def test_hace_obligatorio_el_campo_skill_version(self) -> None:
        # Cubre: T-44
        texto = PROMPT.read_text(encoding="utf-8")
        regla = "skill_version es obligatorio y va exactamente como en el ejemplo: PRECISO_V6.1.1. Nunca lo omitas ni lo cambies."
        self.assertIn(regla, texto)
        self.assertGreater(texto.index(regla), texto.index("Reglas del JSON"))        # está en las reglas, no en el ejemplo
        self.assertLess(len(texto), 13000)


if __name__ == "__main__":
    unittest.main()
