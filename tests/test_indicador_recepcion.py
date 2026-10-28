"""El chip «Revisión de correos» mide al receptor Gmail cuando el puente general no corre (T-84, D-180)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.infra import settings, store, work_heartbeat
from tests.test_openapi import request

AHORA = datetime(2026, 10, 21, 15, 50, 0).astimezone()
CUENTA = "ankacomercialia@gmail.com"
WORK = {"active_mode": "chatgpt_work", "gmail_reception_enabled": True, "work_coordination_enabled": True}


def marca(segundos_atras: float) -> str:
    return (AHORA - timedelta(seconds=segundos_atras)).isoformat(timespec="seconds")


class _Datos(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        for parche in (patch.object(settings, "DATA_DIR", self.data), patch.object(work_heartbeat, "_now", return_value=AHORA)):
            parche.start()
            self.addCleanup(parche.stop)

    def control(self, exito: float | None, intento: float | None, errores: int = 0, tipo: str = "") -> None:
        with store.transaccion() as db:
            db.execute("INSERT OR REPLACE INTO recepcion_control(account,history_id,last_success_at,last_attempt_at,"
                       "last_error_kind,consecutive_errors,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (CUENTA, "1", None if exito is None else marca(exito), None if intento is None else marca(intento),
                        tipo, errores, marca(0)))

    def latido(self, segundos_atras: float) -> None:
        datos = {"at": marca(segundos_atras), "pid": 1, "interval": 30}
        (self.data / work_heartbeat.HEARTBEAT_FILE).write_text(json.dumps(datos), encoding="utf-8")

    def estado(self, config: dict | None = None) -> tuple[str, str]:
        with patch.object(settings, "load_config", return_value=config if config is not None else WORK):
            return work_heartbeat.bridge_state()


# Cubre: T-84
class IndicadorDeRecepcionTests(_Datos):
    def test_01_receptor_activo_con_ultimo_exito_reciente(self) -> None:
        self.control(exito=30, intento=30)
        estado, texto = self.estado()
        self.assertEqual(estado, "ok")
        self.assertIn("La revisión de correos está activa", texto)
        self.assertIn("hace 30 s", texto)

    def test_02_receptor_realmente_detenido_sin_intentos_nuevos(self) -> None:
        self.control(exito=3 * 3600, intento=3 * 3600)
        estado, texto = self.estado()
        self.assertEqual(estado, "stopped")
        self.assertIn("hace 3 h", texto)
        self.assertIn("Iniciar Anka.bat", texto)
        self.assertNotIn("Puente Work", texto)

    def test_03_ultimo_exito_vencido_segun_el_intervalo_configurado(self) -> None:
        self.control(exito=400, intento=20)
        self.assertEqual(self.estado({**WORK, "gmail_reception_interval_seconds": 60})[0], "stopped")   # 400 s > 3 × 60
        self.assertEqual(self.estado({**WORK, "gmail_reception_interval_seconds": 300})[0], "ok")       # 400 s < 3 × 300
        self.control(exito=170, intento=20)
        self.assertEqual(self.estado()[0], "ok")                       # el mínimo de 180 s evita falsas alarmas

    def test_04_errores_consecutivos_con_un_intento_reciente(self) -> None:
        self.control(exito=900, intento=20, errores=4, tipo="red")
        estado, texto = self.estado()
        self.assertEqual(estado, "error")
        self.assertIn("4 vez", texto)
        self.assertIn("red", texto)

    def test_04b_errores_pero_sin_intentos_recientes_es_una_detencion(self) -> None:
        self.control(exito=3600, intento=3000, errores=9, tipo="red")
        self.assertEqual(self.estado()[0], "stopped")

    def test_05_recepcion_sin_historial_no_inventa_actividad(self) -> None:
        self.assertEqual(self.estado()[0], "never")                    # ni siquiera existe la base
        self.assertFalse((self.data / "quotes.sqlite3").exists())      # leer el indicador no la crea
        with store.transaccion():
            pass
        self.assertEqual(self.estado()[0], "never")                    # base sin fila de control
        self.control(exito=None, intento=None)
        self.assertEqual(self.estado()[0], "never")                    # fila vacía
        self.control(exito=None, intento=10)
        estado, texto = self.estado()
        self.assertEqual(estado, "never")
        self.assertIn("ya lo intentó", texto)

    def test_06_con_el_puente_general_en_marcha_manda_su_latido(self) -> None:
        self.control(exito=30, intento=30)                             # el receptor está sano, pero no es lo que se mide
        config = {**WORK, "work_general_bridge_enabled": True, "work_coordination_enabled": False}
        self.assertEqual(self.estado(config)[0], "never")              # sin latido
        self.latido(3 * 3600)
        estado, texto = self.estado(config)
        self.assertEqual(estado, "stopped")
        self.assertIn("Puente Work - Dashboard.bat", texto)

    def test_07_coordinacion_activa_con_el_puente_general_apagado_o_desplazado(self) -> None:
        self.latido(3 * 3600)                                          # el latido viejo del puente ya no cuenta
        self.control(exito=30, intento=30)
        self.assertEqual(self.estado(WORK)[0], "ok")
        self.assertEqual(self.estado({**WORK, "work_general_bridge_enabled": True})[0], "ok")   # la coordinación lo desplaza
        self.assertEqual(self.estado({**WORK, "work_general_bridge_enabled": False})[0], "ok")

    def test_08_otro_modo_o_sin_recepcion_conserva_el_comportamiento_del_latido(self) -> None:
        self.control(exito=30, intento=30)
        self.latido(3 * 3600)
        self.assertEqual(self.estado({**WORK, "gmail_reception_enabled": False})[0], "stopped")
        self.assertEqual(self.estado({**WORK, "active_mode": "local"})[0], "stopped")
        with patch.object(settings, "load_config", side_effect=OSError("sin config")):
            self.assertEqual(work_heartbeat.bridge_state()[0], "stopped")


# Cubre: T-84
class IndicadorEnElDashboardTests(_Datos):
    """El dashboard (`/state`) entrega el texto de cada estado al chip."""

    def pedir(self, config: dict) -> dict:
        with patch.object(settings, "load_config", return_value=config), \
                patch("anka.web.server.load_config", return_value=config):
            return json.loads(request("GET", "/state")["body"])

    def test_09_cada_estado_llega_al_chip_con_su_texto(self) -> None:
        esperado = {"ok": "está activa", "error": "sigue intentándolo", "stopped": "está detenida", "never": "aún no ha empezado"}
        casos = {"ok": (30, 30, 0), "error": (900, 20, 3), "stopped": (3 * 3600, 3 * 3600, 0)}
        for estado, (exito, intento, errores) in casos.items():
            self.control(exito=exito, intento=intento, errores=errores, tipo="red")
            respuesta = self.pedir(WORK)
            self.assertEqual(respuesta["bridge"], estado)
            self.assertIn(esperado[estado], respuesta["text"])
        with store.transaccion() as db:
            db.execute("DELETE FROM recepcion_control")
        respuesta = self.pedir(WORK)
        self.assertEqual(respuesta["bridge"], "never")
        self.assertIn(esperado["never"], respuesta["text"])

    def test_10_fuera_del_modo_work_no_hay_chip(self) -> None:
        self.assertEqual(self.pedir({"active_mode": "local"})["bridge"], "")


if __name__ == "__main__":
    unittest.main()
