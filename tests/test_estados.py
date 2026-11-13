"""Las transiciones se comprueban contra la tabla, pero solo se registran: nunca se bloquea.

Cubre: U-46
"""

from tests import _isolate  # noqa: F401

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import estados, quotes, work_import
from tests.test_work_sync import b64, payload

TABLA = json.loads((Path(__file__).resolve().parents[1] / "spec" / "transiciones.json").read_text(encoding="utf-8"))


class Tabla(unittest.TestCase):
    def test_cada_fila_de_la_tabla_es_prevista_para_su_evento(self):
        for fila in TABLA["transiciones"]:
            if fila["hacia"] == "error":
                continue
            for desde in fila["desde"]:
                hacia = desde if fila["hacia"] == "igual" else fila["hacia"]
                self.assertTrue(estados.prevista(fila["evento"], desde, hacia), fila["id"])

    def test_los_eventos_que_faltaban_estan_en_la_tabla(self):
        eventos = {fila["evento"] for fila in TABLA["transiciones"]}
        self.assertLessEqual({"crear_carpeta", "guardar_borrador", "unir_casos", "rescatar", "hilo_no_disponible",
                              "iniciar_envio", "terminar_envio", "analisis_python"}, eventos)
        sending = [f for f in TABLA["transiciones"] if "sending" in f["desde"] and f["hacia"] != "igual"]
        self.assertTrue(sending)       # la salida de «sending» está descrita

    def test_una_transicion_que_no_esta_no_es_prevista(self):
        self.assertFalse(estados.prevista("crear_carpeta", "ignored", "ready"))
        self.assertFalse(estados.prevista("evento_inventado", "review", "ready"))

    def test_quedarse_igual_nunca_es_un_cambio(self):
        self.assertTrue(estados.prevista("evento_inventado", "review", "review"))


class SoloRegistra(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="T1", message="m1", status="En análisis"))))

    def estado(self):
        db = store.connect()
        try:
            return db.execute("SELECT status FROM quotes WHERE thread_id='T1'").fetchone()[0]
        finally:
            db.close()

    def eventos(self):
        db = store.connect()
        try:
            return [r["summary"] for r in db.execute(
                "SELECT summary FROM case_events WHERE kind='transicion_no_prevista'")]
        finally:
            db.close()

    def test_una_transicion_no_prevista_se_permite_y_se_registra(self):
        db = store.connect()
        with self.assertLogs(estados.log, level="WARNING") as registro:
            previsto = estados.cambiar_estado(db, "T1", QuoteStatus.READY, "crear_carpeta")   # review -> ready
        db.commit()
        db.close()
        self.assertFalse(previsto)
        self.assertEqual(self.estado(), "ready")                       # no se bloqueó
        self.assertEqual(len(self.eventos()), 1)
        self.assertIn("review", self.eventos()[0])
        self.assertIn("ready", self.eventos()[0])
        self.assertIn("crear_carpeta", self.eventos()[0])
        self.assertIn("T1", registro.output[0])

    def test_una_transicion_prevista_no_deja_rastro(self):
        db = store.connect()
        previsto = estados.cambiar_estado(db, "T1", QuoteStatus.READY_REVIEW, "decidir_revision_tecnica")
        db.commit()
        db.close()
        self.assertTrue(previsto)
        self.assertEqual(self.estado(), "ready_review")
        self.assertEqual(self.eventos(), [])

    def test_un_caso_que_no_existe_no_tiene_transicion(self):
        db = store.connect()
        self.assertTrue(estados.verificar_transicion(db, "no-existe", "ready", "crear_carpeta"))
        db.close()

    def test_los_servicios_principales_usan_la_tabla_sin_cambiar_su_comportamiento(self):
        quotes.resolve_technical_review("T1", "sufficient")        # review -> ready_review: prevista
        self.assertEqual(self.estado(), "ready_review")
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="T1", message="m2", status="Faltan datos"))))
        self.assertEqual(self.estado(), "review_pending")
        self.assertEqual(self.eventos(), [])


if __name__ == "__main__":
    unittest.main()
