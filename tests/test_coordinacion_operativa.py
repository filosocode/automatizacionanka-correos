"""Coordinación Work, operación controlada: alertas, hoja desaparecida, un solo importador y verificación previa (T-55).

SQLite temporal, Gmail y Drive simulados y mensajes ficticios; nada toca Google ni datos comerciales reales.

Cubre: T-55
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import contextlib
import copy
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from anka.cli import supervisor, work_coordinacion, work_jobs
from anka.cli import work_sync as sync
from anka.dominio.coordinacion_work import ALERTA, problemas_de_configuracion
from anka.infra import settings, store
from anka.servicios import coordinacion, trabajos_work
from anka.servicios.coordinacion import Coordinador
from tests.test_coordinacion import Base, plan
from tests.test_settings import VALID
from tests.test_trabajos_work import ErrorHttp


class AlertasTests(Base):
    def setUp(self):
        super().setUp()
        self.ahora = datetime.now().astimezone()
        self.coordinador = self.nuevo_con_fecha()
        self.fila("T1", "m1")
        self.ciclo()                                   # publica
        self.job = self.trabajo_de("m1")["job_id"]

    def nuevo_con_fecha(self):
        return Coordinador(self.drive, self.importar_simulado, self.datos / "coordinacion.lock", self.reloj,
                           fecha=lambda: self.ahora, puente_activo=lambda: False)

    def ciclo_en(self, minutos):
        self.ahora = datetime.now().astimezone() + timedelta(minutes=minutos)
        return self.ciclo(alerta_minutos=120)

    def test_dentro_del_plazo_no_hay_alerta(self):
        informe = self.ciclo_en(60)
        self.assertEqual((informe["alertas"], informe["sin_respuesta"]), ([], []))
        self.assertEqual(self.eventos("coordinacion_sin_respuesta"), [])

    def test_pasado_el_plazo_alerta_una_sola_vez_aunque_pasen_ciclos_y_reinicios(self):
        with self.assertLogs(coordinacion.log, "WARNING") as registro:
            primero = self.ciclo_en(180)
            segundo = self.ciclo_en(181)
            self.coordinador = self.nuevo_con_fecha()                         # reinicio
            tercero = self.ciclo_en(240)
        self.assertEqual(primero["alertas"], [self.job])
        self.assertEqual((segundo["alertas"], tercero["alertas"]), ([], []))
        self.assertEqual([primero["sin_respuesta"], segundo["sin_respuesta"], tercero["sin_respuesta"]],
                         [[self.job]] * 3)
        self.assertEqual(len([r for r in registro.output if "sin respuesta de Work" in r]), 1)
        evento, = self.eventos("coordinacion_sin_respuesta", self.job)
        self.assertNotIn("CUERPO", evento["detail_json"])
        self.assertEqual(self.estado_de("m1"), "publicado")                    # alertar no cambia el estado

    def test_si_work_responde_tarde_se_importa_igual_y_deja_de_estar_sin_respuesta(self):
        self.ciclo_en(180)
        self.work_responde("m1")
        informe = self.ciclo_en(185)
        self.assertEqual(informe["importaciones"][0]["resultado"], "importado")
        self.assertEqual(self.estado_de("m1"), "conciliado")
        self.assertEqual(self.ciclo_en(190)["sin_respuesta"], [])

    def test_el_plazo_es_configurable_y_se_valida(self):
        self.assertEqual(ALERTA, (15, 1440, 120))
        informe = self.ciclo_en(30)
        self.ahora = datetime.now().astimezone() + timedelta(minutes=30)
        corto = self.coordinador.ciclo(plan(alerta_minutos=15))
        self.assertEqual((informe["sin_respuesta"], corto["sin_respuesta"]), ([], [self.job]))
        for malo in (14, 1441, "60", True):
            self.assertTrue(problemas_de_configuracion({**VALID, "work_coordination_alert_minutes": malo}), malo)
        self.assertEqual(problemas_de_configuracion({**VALID, "work_coordination_alert_minutes": 15}), [])

    def test_anotar_evento_devuelve_verdadero_solo_la_primera_vez(self):
        self.assertTrue(trabajos_work.anotar_evento(self.job, "prueba_unica", {}))
        self.assertFalse(trabajos_work.anotar_evento(self.job, "prueba_unica", {}))
        self.assertFalse(trabajos_work.anotar_evento("no-existe", "prueba_unica", {}))


class HojaDesaparecidaTests(Base):
    def setUp(self):
        super().setUp()
        self.coordinador = Coordinador(self.drive, self.importar_simulado, self.datos / "coordinacion.lock",
                                       self.reloj, puente_activo=lambda: False)
        self.fila("T1", "m1")
        self.ciclo()
        self.job = self.trabajo_de("m1")["job_id"]
        self.hoja_id = self.trabajo_de("m1")["sheet_file_id"]

    def esperar_importacion(self):
        self.t += 10

    def test_una_hoja_en_la_papelera_se_recupera_sin_duplicar(self):
        self.drive.archivos[self.hoja_id]["trashed"] = True
        informe = self.ciclo()
        self.assertEqual(informe["recuperadas"][0]["job_id"], self.job)
        vivas = self.hojas()
        self.assertEqual(len(vivas), 1)
        self.assertEqual(self.padres(vivas[0]), self.drive.id_de("PENDIENTES"))
        self.assertEqual(self.estado_de("m1"), "publicado")
        for _ in range(3):
            self.ciclo()
        self.assertEqual((len(self.hojas()), len(self.eventos("coordinacion_hoja_ausente", self.job))), (1, 1))
        self.assertEqual(self.drive.archivos[self.hoja_id]["trashed"], True)    # nunca se borra ni se restaura

    def test_una_hoja_borrada_o_sin_la_marca_tambien_se_recupera(self):
        for variante in ("borrada", "sin_marca"):
            with self.subTest(variante):
                actual = self.trabajo_de("m1")["sheet_file_id"]
                if variante == "borrada":
                    del self.drive.archivos[actual]
                else:
                    self.drive.archivos[actual]["props"] = {}
                self.ciclo()
                nueva = self.trabajo_de("m1")["sheet_file_id"]
                self.assertEqual(len(self.drive.vivos(self.job)), 1)     # la sin marca queda: nunca se borra
                self.assertEqual(self.drive.archivos[nueva]["props"].get("anka_job_id"), self.job)
                self.assertEqual(self.estado_de("m1"), "publicado")

    def test_si_la_hoja_marcada_existe_pero_el_registro_apunta_a_otra_se_adopta_sin_crear(self):
        creados = self.drive.creados
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET sheet_file_id='fantasma' WHERE job_id=?", (self.job,))
        self.ciclo()
        self.assertEqual((self.drive.creados, len(self.hojas())), (creados, 1))
        self.assertEqual(self.trabajo_de("m1")["sheet_file_id"], self.hoja_id)

    def test_dos_hojas_con_la_marca_dejan_el_trabajo_ambiguo_sin_borrar_nada(self):
        self.drive.archivos[self.hoja_id]["trashed"] = True
        pendientes = self.drive.id_de("PENDIENTES")[0]
        self.drive.sembrar(self.job, "copia a", carpeta=pendientes)
        self.drive.sembrar(self.job, "copia b", carpeta=pendientes)
        antes = len(self.drive.archivos)
        informe = self.ciclo()
        self.assertEqual(informe["recuperadas"][0]["resultado"], "ambiguo")
        self.assertEqual((self.estado_de("m1"), len(self.drive.archivos), self.drive.borrados), ("ambiguo", antes, 0))
        self.ciclo()
        self.assertEqual(self.estado_de("m1"), "ambiguo")                       # nunca se elige ni se insiste

    def test_un_error_de_drive_al_comprobar_no_se_toma_por_ausencia(self):
        with patch.object(trabajos_work, "hoja_guardada", side_effect=ErrorHttp(500)):
            informe = self.ciclo()
        self.assertIn("hoja:ErrorHttp", informe["errores"])
        self.assertEqual((informe["recuperadas"], self.eventos("coordinacion_hoja_ausente")), ([], []))
        self.assertEqual(len(self.hojas()), 1)

    def test_la_hoja_que_reaparece_deja_de_marcarse_para_recuperar(self):
        self.drive.archivos[self.hoja_id]["trashed"] = True
        with patch("anka.servicios.despacho.despachar_mensaje", side_effect=ErrorHttp(503)):
            self.ciclo()
        self.assertIn(self.job, self.coordinador.por_recuperar)
        self.drive.archivos[self.hoja_id]["trashed"] = False
        self.ciclo()
        self.assertEqual(self.coordinador.por_recuperar, set())
        self.assertEqual(len(self.hojas()), 1)

    def test_un_fallo_de_drive_al_recuperar_se_reintenta_en_el_ciclo_siguiente(self):
        self.drive.archivos[self.hoja_id]["trashed"] = True
        self.drive.fallar_listado = ErrorHttp(500)
        primero = self.ciclo()
        self.assertTrue(primero["errores"])
        self.drive.fallar_listado = None
        self.ciclo()
        self.assertEqual(len(self.hojas()), 1)
        self.assertEqual(self.estado_de("m1"), "publicado")

    def test_una_hoja_que_desaparece_una_y_otra_vez_termina_para_una_persona(self):
        for _ in range(3):
            self.drive.archivos[self.trabajo_de("m1")["sheet_file_id"]]["trashed"] = True
            self.ciclo()
        self.assertIn(self.job, self.coordinador.detenidos)
        self.assertEqual(len(self.eventos("coordinacion_requiere_persona", self.job)), 1)
        self.drive.archivos[self.trabajo_de("m1")["sheet_file_id"]]["trashed"] = True
        self.assertEqual(self.ciclo()["recuperadas"], [])

    def test_un_reinicio_no_repite_el_evento_ni_duplica_la_hoja(self):
        self.drive.archivos[self.hoja_id]["trashed"] = True
        self.ciclo()
        self.coordinador = Coordinador(self.drive, self.importar_simulado, self.datos / "coordinacion.lock",
                                       self.reloj, puente_activo=lambda: False)
        self.drive.archivos[self.trabajo_de("m1")["sheet_file_id"]]["trashed"] = True
        self.ciclo()
        self.assertEqual(len(self.eventos("coordinacion_hoja_ausente", self.job)), 1)
        self.assertEqual(len(self.hojas()), 1)

    def test_no_se_comprueba_la_hoja_si_ya_hay_borrador(self):
        self.work_responde("m1")
        with patch.object(trabajos_work, "hoja_guardada", side_effect=AssertionError("no debe comprobarse")):
            informe = self.ciclo()
        self.assertEqual(informe["importaciones"][0]["resultado"], "importado")


class UnSoloImportadorTests(Base):
    def setUp(self):
        super().setUp()
        self.fila("T1", "m1")
        self.ciclo()

    def test_con_un_puente_general_vivo_el_ciclo_no_importa_y_avisa_una_vez(self):
        self.work_responde("m1")
        coordinador = Coordinador(self.drive, self.importar_simulado, self.datos / "coordinacion.lock", self.reloj,
                                  puente_activo=lambda: True)
        with self.assertLogs(coordinacion.log, "WARNING") as registro:
            for _ in range(3):
                informe = coordinador.ciclo(plan())
        self.assertEqual(self.llamadas, [])
        self.assertIn("puente_general_activo", informe["errores"])
        self.assertEqual(len([r for r in registro.output if "puente general" in r]), 1)
        self.assertEqual(self.estado_de("m1"), "publicado")
        coordinador.puente_activo = lambda: False                               # el puente se detuvo
        coordinador.ciclo(plan())
        self.assertEqual(self.estado_de("m1"), "conciliado")

    def test_el_latido_real_de_otro_proceso_vivo_cuenta_como_puente(self):
        (self.datos / "work_sync_heartbeat.json").write_text(
            json.dumps({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "pid": os.getppid()}),
            encoding="utf-8")
        coordinador = Coordinador(self.drive, self.importar_simulado, self.datos / "coordinacion.lock", self.reloj)
        self.assertIn("puente_general_activo", coordinador.ciclo(plan())["errores"])
        (self.datos / "work_sync_heartbeat.json").unlink()
        self.assertNotIn("puente_general_activo", coordinador.ciclo(plan())["errores"])

    def test_el_puente_general_manual_se_niega_con_la_coordinacion_activa(self):
        activa = {**copy.deepcopy(VALID), "active_mode": "chatgpt_work", "work_coordination_enabled": True}
        apagada = {**copy.deepcopy(VALID), "active_mode": "chatgpt_work"}
        with patch.object(sync, "load_config", return_value=activa):
            self.assertTrue(sync.coordinacion_activa(activa))
            with patch.object(sys, "argv", ["work_sync", "--once"]), self.assertRaises(SystemExit) as salida, \
                    contextlib.redirect_stdout(io.StringIO()) as texto:
                sync.main()
            self.assertEqual(salida.exception.code, sync.EXIT_STOP)
            self.assertIn("único importador", texto.getvalue())
        with patch.object(sync, "load_config", return_value=apagada):
            self.assertFalse(sync.coordinacion_activa(apagada))

    def test_la_importacion_aislada_manual_sigue_permitida(self):
        activa = {**copy.deepcopy(VALID), "active_mode": "chatgpt_work", "work_coordination_enabled": True}
        aislada = MagicMock(side_effect=SystemExit(0))
        with patch.object(sync, "load_config", return_value=activa), \
                patch.object(sync, "_importacion_aislada", aislada), \
                patch.object(sys, "argv", ["work_sync", "--once", "--only-thread-id", "T1", "--only-message-id", "m1"]), \
                self.assertRaises(SystemExit) as salida:
            sync.main()
        self.assertEqual(salida.exception.code, 0)
        aislada.assert_called_once()

    def test_el_lanzador_no_arranca_el_puente_general_con_la_coordinacion_activa(self):
        self.assertTrue(supervisor.puente_general_corre(True, False, True))
        self.assertFalse(supervisor.puente_general_corre(True, True, True))
        self.assertFalse(supervisor.puente_general_corre(False, False, True))
        self.assertFalse(supervisor.puente_general_corre(False, True, True))

    def test_un_solo_importador_aunque_el_ciclo_se_repita_o_se_lance_dos_veces(self):
        self.work_responde("m1")
        for _ in range(3):
            self.nuevo().ciclo(plan())
        self.assertEqual(self.llamadas, [("T1", "m1")])
        self.assertEqual(self.estado_de("m1"), "conciliado")
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 1)


class VerificacionPrevia(Base):
    def setUp(self):
        super().setUp()
        self.config = {**copy.deepcopy(VALID), "active_mode": "chatgpt_work", "gmail_reception_enabled": True,
                       "work_coordination_folder_id": "raiz",
                       "work_coordination_since": "2026-10-17T00:00:00-05:00",
                       "work_coordination_thread_id": "T1", "work_coordination_message_id": "m1"}
        self.fila("T1", "m1")

    def verificar(self, procesos=(), **cambios):
        config = {**self.config, **cambios}
        with patch("anka.cli.work_coordinacion.services", side_effect=AssertionError("no debe tocar Google")):
            return work_coordinacion.verificar_piloto(config, list(procesos))

    def proceso(self, linea, pid=4001):
        return {"ProcessId": pid, "ParentProcessId": 1, "CommandLine": linea}

    def conteos(self):
        with store.lectura() as db:
            return [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("recepcion_mensajes", "recepcion_eventos", "trabajos_work", "quotes")]

    def test_todo_en_orden_el_piloto_queda_listo_sin_escribir_ni_usar_google(self):
        antes, config_antes = self.conteos(), json.dumps(self.config, sort_keys=True)
        resultado = self.verificar()
        self.assertEqual((resultado["bloqueos"], resultado["listo"], resultado["activada"]), ([], True, False))
        self.assertTrue(any("apagada" in a for a in resultado["avisos"]))
        self.assertTrue(any("tarea programada de Work" in a for a in resultado["avisos"]))
        self.assertEqual(self.conteos(), antes)
        self.assertEqual(json.dumps(self.config, sort_keys=True), config_antes)
        self.assertEqual(resultado["cola"]["pendientes"], 1)
        self.assertEqual(resultado["cola"]["elegibles_nuevos"], 1)
        self.assertEqual(resultado["cola"]["mensaje_de_laboratorio"]["elegible"], True)
        texto = json.dumps(resultado, ensure_ascii=False)
        self.assertNotIn("CUERPO SECRETO", texto)
        self.assertNotIn("Solicitud de levantamiento", texto)

    def test_la_configuracion_efectiva_muestra_modos_y_limites(self):
        efectiva = self.verificar(work_coordination_enabled=True)["configuracion"]
        self.assertEqual((efectiva["active_mode"], efectiva["work_coordination_enabled"],
                          efectiva["work_coordination_max_publish"] if "work_coordination_max_publish" in self.config
                          else None), ("chatgpt_work", True, None))
        self.assertIs(efectiva["work_coordination_carpeta_definida"], True)
        self.assertIs(efectiva["work_coordination_mensaje_de_laboratorio_definido"], True)
        self.assertTrue(self.verificar(work_coordination_enabled=True)["activada"])

    def test_cada_bloqueo_de_configuracion_se_senala(self):
        casos = {
            "modo": {"active_mode": "local"},
            "despachador doble": {"work_dispatch_enabled": True, "work_dispatch_folder_id": "x",
                                  "work_dispatch_thread_id": "T1", "work_dispatch_message_id": "m1"},
            "sin carpeta": {"work_coordination_folder_id": ""},
            "sin corte": {"work_coordination_since": ""},
            "corte sin zona": {"work_coordination_since": "2026-10-17T00:00:00"},
            "sin laboratorio": {"work_coordination_thread_id": "", "work_coordination_message_id": ""},
            "intervalo malo": {"work_coordination_interval_seconds": 5},
        }
        for nombre, cambio in casos.items():
            with self.subTest(nombre):
                resultado = self.verificar(**cambio)
                self.assertFalse(resultado["listo"])
                self.assertTrue(resultado["bloqueos"])

    def test_la_recepcion_apagada_es_un_aviso_no_un_bloqueo(self):
        resultado = self.verificar(gmail_reception_enabled=False)
        self.assertTrue(resultado["listo"])
        self.assertTrue(any("recepción de Gmail está apagada" in a for a in resultado["avisos"]))

    def test_procesos_que_compiten_bloquean_y_los_demas_avisan(self):
        bloqueantes = {
            "puente general": "python.exe work_dashboard_sync.py --seconds 30",
            "ciclo manual": "python.exe -m anka.cli.work_jobs coordinar --una-vez --confirmar",
            "despacho manual": "python.exe -m anka.cli.work_jobs despachar --carpeta-id x",
        }
        for nombre, linea in bloqueantes.items():
            with self.subTest(nombre):
                self.assertFalse(self.verificar([self.proceso(linea)])["listo"])
        dos_receptores = [self.proceso("python.exe -m anka.cli.gmail_reception --interval 60", 4002),
                          self.proceso("python.exe supervisor.py --servicio", 4003)]
        self.assertFalse(self.verificar(dos_receptores)["listo"])
        solo_lanzador = self.verificar([self.proceso("python.exe supervisor.py --servicio")])
        self.assertTrue(solo_lanzador["listo"])
        self.assertTrue(any("lanzador está corriendo" in a for a in solo_lanzador["avisos"]))
        self.assertEqual(solo_lanzador["procesos"], {"lanzador": 1})
        aislada = self.verificar([self.proceso(
            "python.exe -m anka.cli.work_sync --once --only-thread-id T --only-message-id m")])
        self.assertTrue(aislada["listo"])
        self.assertEqual(aislada["procesos"], {"importacion_aislada": 1})

    def test_la_orden_que_verifica_no_se_cuenta_a_si_misma(self):
        propio = [{"ProcessId": os.getpid(), "ParentProcessId": 10,
                   "CommandLine": "python.exe -m anka.cli.work_jobs coordinar --una-vez"},
                  {"ProcessId": 10, "ParentProcessId": 1, "CommandLine": "python.exe supervisor.py"}]
        self.assertEqual(work_coordinacion.clasificar_procesos(propio, os.getpid()), {})

    def test_un_candado_o_un_latido_de_otro_proceso_vivo_bloquean(self):
        (self.datos / "work_coordinacion.lock").write_text(json.dumps({"pid": os.getppid(), "token": "x"}),
                                                           encoding="utf-8")
        self.assertFalse(self.verificar()["listo"])
        (self.datos / "work_coordinacion.lock").write_text(json.dumps({"pid": 99999999, "token": "x"}),
                                                           encoding="utf-8")
        self.assertTrue(self.verificar()["listo"])                              # el proceso ya no existe
        (self.datos / "work_sync_heartbeat.json").write_text(
            json.dumps({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "pid": os.getppid()}),
            encoding="utf-8")
        self.assertFalse(self.verificar()["listo"])

    def test_el_mensaje_de_laboratorio_ausente_antiguo_o_no_elegible_bloquea(self):
        self.assertFalse(self.verificar(work_coordination_message_id="no-esta")["listo"])
        self.pasar_a_antes_del_corte("m1")
        resultado = self.verificar(work_coordination_since="2026-10-17T00:00:00-05:00")
        self.assertTrue(any("antes del corte" in b for b in resultado["bloqueos"]))
        self.assertEqual(resultado["cola"]["fuera_del_corte"], 1)
        self.fila("T2", "ruido", remitente="noreply@tm.openai.com", asunto="Tu tarea terminó")
        malo = self.verificar(work_coordination_thread_id="T2", work_coordination_message_id="ruido")
        self.assertTrue(any("no es elegible" in b for b in malo["bloqueos"]))

    def test_si_el_mensaje_ya_tiene_trabajo_avisa_y_muestra_los_sin_respuesta(self):
        self.ciclo()
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET published_at='2026-10-01T10:00:00-05:00'")
        resultado = self.verificar(work_coordination_since="2026-10-01T00:00:00-05:00")
        self.assertTrue(resultado["listo"])
        self.assertTrue(any("ya tiene trabajo" in a for a in resultado["avisos"]))
        self.assertEqual(len(resultado["cola"]["sin_respuesta"]), 1)
        self.assertEqual(resultado["cola"]["trabajos_por_estado"], {"publicado": 1})

    def test_la_orden_verificar_piloto_imprime_json_sin_tocar_google_ni_confirmacion(self):
        salida, error = io.StringIO(), io.StringIO()
        with patch.object(settings, "load_config", return_value=self.config), \
                patch("anka.cli.work_coordinacion.listar_procesos", return_value=[]), \
                patch("anka.cli.work_coordinacion.services", side_effect=AssertionError("no debe tocar Google")), \
                contextlib.redirect_stderr(error):
            codigo = work_jobs.main(["verificar-piloto"], salida=lambda texto: salida.write(texto))
        resultado = json.loads(salida.getvalue())
        self.assertEqual((codigo, resultado["listo"]), (0, True))

    def test_sin_base_de_datos_solo_avisa(self):
        for archivo in self.datos.glob("quotes.sqlite3*"):
            with contextlib.suppress(OSError):
                archivo.unlink()
        resultado = self.verificar()
        self.assertTrue(any("base de datos" in a for a in resultado["avisos"]))


class ApagadaPorDefectoTests(unittest.TestCase):
    def test_el_ejemplo_y_el_defecto_dejan_todo_apagado(self):
        from pathlib import Path
        ejemplo = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(ejemplo["work_coordination_enabled"], False)
        self.assertEqual(ejemplo["work_coordination_alert_minutes"], 120)
        self.assertEqual(settings.unknown_config_keys(ejemplo), [])
        self.assertEqual(settings.validate_config(ejemplo), [])


if __name__ == "__main__":
    unittest.main()
