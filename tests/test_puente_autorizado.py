"""El puente general de borradores solo corre con autorización expresa (T-56, D-163).

Reproduce el incidente del piloto 6C: apagar la coordinación con el lanzador activo arrancaba el puente general y procesaba
borradores históricos. Todo con configuración simulada: no se inicia ningún proceso real ni se toca Google ni SQLite.

Cubre: T-56
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import contextlib
import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import supervisor
from anka.cli import work_sync as sync
from anka.dominio import coordinacion_work
from anka.infra import settings
from tests.test_settings import VALID

RAIZ = Path(__file__).resolve().parent.parent
BASE = {"active_mode": "chatgpt_work"}


def con(**claves):
    return {**copy.deepcopy(VALID), **BASE, **claves}


class Hijo:
    """Reemplaza a `Child` del lanzador: cuenta cuántas veces se vigila y se detiene, sin lanzar procesos."""

    creados: dict = {}

    def __init__(self, nombre, args):
        self.name, self.process, self.stopped = nombre, None, False
        self.checks, self.stop_calls = 0, 0
        Hijo.creados[nombre] = self

    def check(self):
        self.checks += 1
        self.process = object()

    def stop(self):
        self.stop_calls += 1
        self.process = None


def correr_lanzador(configs):
    """Ejecuta `servicio()` una vuelta por configuración (se relee en cada vuelta) y devuelve el hijo «Puente Work»."""
    Hijo.creados = {}
    with patch.object(supervisor, "configure_logging"), patch.object(supervisor, "stop_old_instances"), \
            patch.object(supervisor, "Child", Hijo), patch.object(supervisor, "MODE_RECHECK", 0), \
            patch.object(supervisor, "code_snapshot", return_value={"a": 1.0}), \
            patch.object(supervisor, "work_mode", return_value=True), \
            patch.object(supervisor, "work_drive_enabled", return_value=False), \
            patch.object(supervisor, "CopiaDeSeguridad"), patch.object(supervisor, "Reconciliador"), \
            patch.object(supervisor, "ReceptorGmail"), patch.object(supervisor, "CoordinadorWork"), \
            patch.object(supervisor, "run_due_reminders"), \
            patch.object(supervisor.settings, "load_config", side_effect=configs), \
            patch.object(supervisor.time, "sleep", side_effect=[None] * (len(configs) - 1) + [KeyboardInterrupt]):
        assert supervisor.servicio() == 0
    return Hijo.creados["Puente Work"]


class ReglaDelLanzadorTests(unittest.TestCase):
    def test_tabla_de_combinaciones(self):
        # (modo Work, coordinación, autorizado) -> ¿corre el puente general?
        esperado = {
            (True, False, True): True,       # único caso en que corre
            (True, False, False): False,     # sin autorización expresa: nunca (el incidente)
            (True, True, True): False,       # el coordinador es el único importador (D-161)
            (True, True, False): False,
            (False, False, True): False,     # fuera del modo Work no hay puente
            (False, False, False): False,
            (False, True, True): False,
            (False, True, False): False,
        }
        for argumentos, corre in esperado.items():
            self.assertIs(supervisor.puente_general_corre(*argumentos), corre, argumentos)

    def test_el_incidente_apagar_la_coordinacion_ya_no_arranca_el_puente(self):
        # Antes: coordinación true y luego false, sin clave nueva -> el supervisor lanzaba work_dashboard_sync.
        puente = correr_lanzador([con(work_coordination_enabled=True), con(work_coordination_enabled=False),
                                  con(work_coordination_enabled=False)])
        self.assertEqual(puente.checks, 0)
        self.assertIsNone(puente.process)

    def test_sin_clave_o_con_clave_invalida_el_puente_no_corre(self):
        for valor in ("true", 1, None, "si", [], {}):
            puente = correr_lanzador([con(work_general_bridge_enabled=valor)] * 2)
            self.assertEqual(puente.checks, 0, valor)
        self.assertEqual(correr_lanzador([con()] * 2).checks, 0)

    def test_con_autorizacion_expresa_y_sin_coordinacion_el_puente_corre(self):
        puente = correr_lanzador([con(work_general_bridge_enabled=True)] * 3)
        self.assertEqual(puente.checks, 3)

    def test_con_la_coordinacion_activa_el_puente_sigue_excluido_aunque_este_autorizado(self):
        puente = correr_lanzador([con(work_general_bridge_enabled=True, work_coordination_enabled=True)] * 3)
        self.assertEqual(puente.checks, 0)

    def test_encender_la_coordinacion_detiene_un_puente_autorizado_en_marcha(self):
        puente = correr_lanzador([con(work_general_bridge_enabled=True),
                                  con(work_general_bridge_enabled=True, work_coordination_enabled=True)])
        self.assertEqual(puente.checks, 1)
        self.assertGreaterEqual(puente.stop_calls, 1)

    def test_retirar_la_autorizacion_detiene_el_puente_en_marcha(self):
        puente = correr_lanzador([con(work_general_bridge_enabled=True), con(work_general_bridge_enabled=False)])
        self.assertEqual(puente.checks, 1)
        self.assertGreaterEqual(puente.stop_calls, 1)

    def test_una_configuracion_ilegible_apaga_el_puente(self):
        from anka.dominio.errores import ErrorDeAnka
        puente = correr_lanzador([con(work_general_bridge_enabled=True), ErrorDeAnka("config dañada")])
        self.assertEqual(puente.checks, 1)
        self.assertGreaterEqual(puente.stop_calls, 1)


class PuenteManualTests(unittest.TestCase):
    def ejecutar(self, config, *argv):
        with patch.object(sync, "load_config", return_value=config), patch.object(sync, "configure_logging"), \
                patch.object(sync, "other_bridge_running", return_value=False), \
                patch.object(sync, "services", side_effect=AssertionError("no debía abrir Gmail")), \
                patch.object(sys, "argv", ["work_dashboard_sync.py", *argv]), \
                self.assertRaises(SystemExit) as salida, contextlib.redirect_stdout(io.StringIO()) as texto:
            sync.main()
        return salida.exception.code, texto.getvalue()

    def test_sin_autorizacion_el_puente_manual_se_niega_sin_abrir_gmail(self):
        for argumentos in (("--once",), ()):
            codigo, texto = self.ejecutar(con(), *argumentos)
            self.assertEqual(codigo, sync.EXIT_STOP)
            self.assertIn("work_general_bridge_enabled", texto)

    def test_con_la_clave_mal_escrita_tambien_se_niega(self):
        codigo, _ = self.ejecutar(con(work_general_bridge_enabled="true"), "--once")
        self.assertEqual(codigo, sync.EXIT_STOP)

    def test_con_la_coordinacion_activa_se_niega_aunque_este_autorizado(self):
        codigo, texto = self.ejecutar(con(work_general_bridge_enabled=True, work_coordination_enabled=True), "--once")
        self.assertEqual(codigo, sync.EXIT_STOP)
        self.assertIn("único importador", texto)

    def test_motivo_de_bloqueo_por_combinacion(self):
        self.assertIsNone(sync.motivo_de_bloqueo(con(work_general_bridge_enabled=True)))
        self.assertEqual(sync.motivo_de_bloqueo(con()), sync.MENSAJE_SIN_AUTORIZACION)
        self.assertEqual(sync.motivo_de_bloqueo(con(work_general_bridge_enabled=True, work_coordination_enabled=True)),
                         sync.MENSAJE_COORDINACION)

    def test_la_importacion_aislada_no_necesita_autorizacion(self):
        aislada = MagicMock(side_effect=SystemExit(0))
        with patch.object(sync, "load_config", return_value=con()), patch.object(sync, "configure_logging"), \
                patch.object(sync, "_importacion_aislada", aislada), \
                patch.object(sys, "argv", ["work_sync", "--once", "--only-thread-id", "T1", "--only-message-id", "m1"]), \
                self.assertRaises(SystemExit) as salida:
            sync.main()
        self.assertEqual(salida.exception.code, 0)
        aislada.assert_called_once()

    def test_con_autorizacion_el_puente_manual_llega_a_abrir_gmail(self):
        with patch.object(sync, "load_config", return_value=con(work_general_bridge_enabled=True)), \
                patch.object(sync, "configure_logging"), patch.object(sync, "other_bridge_running", return_value=False), \
                patch.object(sync, "services", side_effect=RuntimeError("llegó a Gmail")), \
                patch.object(sync, "write_heartbeat"), patch.object(sync, "wait_for_next_cycle"), \
                patch.object(sys, "argv", ["work_dashboard_sync.py", "--once"]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as salida:
            sync.main()
        self.assertNotEqual(salida.exception.code, sync.EXIT_STOP)      # no lo detuvo la protección


class ConfiguracionTests(unittest.TestCase):
    def test_la_clave_es_conocida_y_booleana(self):
        self.assertIn("work_general_bridge_enabled", settings.KNOWN_CONFIG_KEYS)
        self.assertEqual(settings.validate_config(con(work_general_bridge_enabled=True)), [])
        self.assertEqual(settings.validate_config(con(work_general_bridge_enabled=False)), [])
        self.assertEqual(settings.validate_config(con()), [])
        for valor in ("true", 1, None, []):
            problemas = settings.validate_config(con(work_general_bridge_enabled=valor))
            self.assertTrue(any("work_general_bridge_enabled" in p for p in problemas), valor)

    def test_valores_conservadores_ante_ausente_o_invalido(self):
        for config in ({}, {"work_general_bridge_enabled": None}, {"work_general_bridge_enabled": "true"},
                       {"work_general_bridge_enabled": 1}, {"work_general_bridge_enabled": False}):
            self.assertFalse(coordinacion_work.puente_general_habilitado(config), config)
        self.assertTrue(coordinacion_work.puente_general_habilitado({"work_general_bridge_enabled": True}))

    def test_la_configuracion_de_ejemplo_lo_trae_apagado(self):
        ejemplo = json.loads((RAIZ / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(ejemplo["work_general_bridge_enabled"], False)

    def test_autorizarlo_no_activa_la_coordinacion_ni_viceversa(self):
        self.assertFalse(coordinacion_work.plan_desde_config(con(work_general_bridge_enabled=True)))
        self.assertTrue(coordinacion_work.puente_general_habilitado(con(work_general_bridge_enabled=True,
                                                                         work_coordination_enabled=False)))


class DocumentacionTests(unittest.TestCase):
    def test_el_procedimiento_de_cierre_detiene_primero_los_servicios(self):
        texto = (RAIZ / "docs" / "PILOTO_6C_ACTIVACION.md").read_text(encoding="utf-8")
        cierre = texto[texto.index("## 8. Reversión"):]
        self.assertLess(cierre.index("detén el lanzador"), cierre.index("work_coordination_enabled=false"))
        self.assertIn("work_general_bridge_enabled", texto)

    def test_la_operacion_normal_documenta_como_volver_a_habilitarlo(self):
        texto = (RAIZ / "docs" / "MODO_OPERACION.md").read_text(encoding="utf-8")
        self.assertIn("work_general_bridge_enabled", texto)


if __name__ == "__main__":
    unittest.main()
