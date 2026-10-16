"""Un caso avanzado no retrocede por un análisis nuevo, igual en los tres modos (U-47, D-112).

El mismo escenario se ejecuta en `local`, `api` (los dos por `RevisorDeHilos`) y `chatgpt_work` (por el puente) y
se comparan los estados que quedan.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus, estado_tras_un_analisis
from anka.infra import settings, store
from anka.servicios import estados, pipeline, work_import
from tests.fake_drive import FakeDriveTree
from tests.test_app import FakeGmail, complete_analysis, encoded_message, test_config
from tests.test_work_sync import b64, payload

INTERNAL_DATE = 1788271200000
CARPETA_VIEJA = "carpeta-vieja"


def _semilla(db, estado, ultimo_mensaje):
    store.save(db, "t-1", ultimo_mensaje, estado, "cliente@example.com", {}, folder_id=CARPETA_VIEJA)


def _datos(temp):
    db = store.connect()
    try:
        fila = db.execute("SELECT status, folder_id FROM quotes WHERE thread_id='t-1'").fetchone()
        no_previstas = db.execute(
            "SELECT COUNT(*) FROM case_events WHERE kind='transicion_no_prevista'").fetchone()[0]
        return {"status": fila["status"], "folder_id": fila["folder_id"], "no_previstas": no_previstas}
    finally:
        db.close()


def _en_python(modo, estado, ultimo_mensaje):
    """Llega el correo `client-1` con un análisis completo a un caso sembrado en `estado`."""
    hilo = {"id": "t-1", "messages": [encoded_message(
        "client-1", "cliente@example.com", "Solicito cotización de un levantamiento de 10 ha.", INTERNAL_DATE)]}
    gmail = FakeGmail(hilo)
    config = test_config() | {"active_mode": modo}
    with tempfile.TemporaryDirectory() as temp, patch.object(settings, "DATA_DIR", Path(temp)), \
            patch("anka.servicios.pipeline.load_config", return_value=config), \
            patch("anka.servicios.pipeline.services", return_value=(gmail, FakeDriveTree({"Minera Sur": {}}))), \
            patch("anka.servicios.pipeline.analizar_hilo", return_value=complete_analysis()):
        db = store.connect()
        _semilla(db, estado, ultimo_mensaje)
        db.close()
        with redirect_stdout(io.StringIO()):
            pipeline.run(True, review_only=False)
        datos = _datos(temp)
    datos["correos_enviados"] = len(gmail.sent_messages)
    return datos


def _en_work(estado, ultimo_mensaje, estado_de_work="Faltan datos"):
    with tempfile.TemporaryDirectory() as temp, patch.object(settings, "DATA_DIR", Path(temp)):
        db = store.connect()
        _semilla(db, estado, ultimo_mensaje)
        db.close()
        work_import.dashboard_upsert(b64(json.dumps(payload(
            thread="t-1", message="client-1", status=estado_de_work, client_email="cliente@example.com"))))
        return _datos(temp)


# Cubre: U-47, R8
class MismaRegla(unittest.TestCase):
    def test_la_funcion_comun(self):
        self.assertEqual(estado_tras_un_analisis("ready", "review_pending", False), "ready")
        self.assertEqual(estado_tras_un_analisis("sending", "ready_review", False), "sending")
        self.assertEqual(estado_tras_un_analisis("response_sent", "review", True), "response_sent")
        self.assertEqual(estado_tras_un_analisis("response_sent", "review", False), "review")
        self.assertEqual(estado_tras_un_analisis("waiting", "review", True), "review")
        self.assertEqual(estado_tras_un_analisis(None, "review", False), "review")

    def test_un_caso_con_carpeta_o_envio_en_curso_no_retrocede_en_ningun_modo(self):
        for estado in (QuoteStatus.READY, QuoteStatus.SENDING):
            with self.subTest(estado=estado):
                work = _en_work(estado, "viejo")
                local = _en_python("local", estado, "viejo")
                api = _en_python("api", estado, "viejo")
                for modo, datos in (("work", work), ("local", local), ("api", api)):
                    self.assertEqual(datos["status"], estado, modo)
                    self.assertEqual(datos["folder_id"], CARPETA_VIEJA, modo)
                    self.assertEqual(datos["no_previstas"], 0, modo)     # conservar el estado no es una transición
                self.assertEqual(local["correos_enviados"], 0)           # y el modo Python no envía ni crea carpeta
                self.assertEqual(api["correos_enviados"], 0)

    def test_response_sent_se_mueve_en_los_tres_modos_solo_con_un_mensaje_nuevo_del_cliente(self):
        for modo_python in ("local", "api"):
            nuevo_py = _en_python(modo_python, QuoteStatus.RESPONSE_SENT, "viejo")
            self.assertNotEqual(nuevo_py["status"], QuoteStatus.RESPONSE_SENT, modo_python)
        self.assertNotEqual(_en_work(QuoteStatus.RESPONSE_SENT, "viejo")["status"], QuoteStatus.RESPONSE_SENT)
        # el mismo mensaje ya analizado no mueve el caso: Work lo protege; el modo Python ni lo vuelve a leer
        self.assertEqual(_en_work(QuoteStatus.RESPONSE_SENT, "client-1")["status"], QuoteStatus.RESPONSE_SENT)
        for modo_python in ("local", "api"):
            self.assertEqual(_en_python(modo_python, QuoteStatus.RESPONSE_SENT, "client-1")["status"],
                             QuoteStatus.RESPONSE_SENT, modo_python)

    def test_un_caso_sin_avanzar_si_se_actualiza_en_los_tres_modos(self):
        for modo in ("local", "api"):
            self.assertNotEqual(_en_python(modo, QuoteStatus.WAITING, "viejo")["status"], QuoteStatus.WAITING)
        self.assertNotEqual(_en_work(QuoteStatus.WAITING, "viejo")["status"], QuoteStatus.WAITING)

    def test_la_tabla_describe_la_proteccion_del_modo_python(self):
        for estado in ("ready", "sending"):
            self.assertTrue(estados.prevista("analisis_python", estado, estado))


if __name__ == "__main__":
    unittest.main()
