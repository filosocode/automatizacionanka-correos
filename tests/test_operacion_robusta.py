"""Rutas de error y reinicios del lanzador, el puente, la línea de comandos, las copias y el modo activo.

Ninguna prueba arranca procesos reales, abre el navegador ni llega a Google: todo lo externo se sustituye.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import app, supervisor
from anka.cli import work_sync as sync
from anka.dominio.configuracion import MODOS, Modo, es_modo_work, modo_activo
from anka.dominio.errores import ErrorDeAnka, ModoNoPermitido
from anka.infra import backup, cache_hallazgos, modes, settings, work_heartbeat

AHORA = datetime(2026, 6, 1, 9, 30, 0).astimezone()


class _DatosTemporales(unittest.TestCase):
    """Cada prueba escribe en su propia carpeta de datos."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        patcher = patch.object(settings, "DATA_DIR", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)


# Cubre: T-11
class ModoActivoTests(unittest.TestCase):
    def test_los_tres_modos_y_el_ayudante_de_work(self):
        self.assertEqual(MODOS, {"local", "api", "chatgpt_work"})
        self.assertEqual(MODOS, settings.ACTIVE_MODES)          # la validación de config.json usa los mismos
        self.assertTrue(es_modo_work({"active_mode": "chatgpt_work"}))
        self.assertFalse(es_modo_work({"active_mode": "local"}))
        self.assertFalse(es_modo_work({}))
        self.assertEqual(modo_activo({}), Modo.LOCAL)          # sin modo se asume el local, como antes
        self.assertEqual(modo_activo({"active_mode": "api"}), "api")
        self.assertEqual(Modo.CHATGPT_WORK, "chatgpt_work")    # se compara igual que su texto

    def test_en_modo_work_el_bot_local_no_corre(self):
        with self.assertRaises(ModoNoPermitido):
            modes.ensure_local_or_api_mode({"active_mode": "chatgpt_work"})
        modes.ensure_local_or_api_mode({})                     # local por defecto: permitido

    def imprimir(self, config):
        out = io.StringIO()
        with patch.object(modes, "load_config", return_value=config), redirect_stdout(out):
            modes.print_mode()
        return out.getvalue()

    def test_print_mode_describe_cada_modo(self):
        llm = {"provider": "openai", "model": "m"}
        self.assertIn("Motor: API (openai, m)", self.imprimir({"active_mode": "api", "llm": llm}))
        self.assertIn("Orquestador local bloqueado", self.imprimir({"active_mode": "chatgpt_work", "llm": llm}))


# Cubre: T-11
class AutomatizacionDeCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"CODEX_HOME": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.archivo = Path(self.temp.name) / "automations" / modes.AUTOMATION_ID / "automation.toml"

    def escribir(self, texto):
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        self.archivo.write_text(texto, encoding="utf-8")

    def test_sin_archivo_no_hay_frecuencia_ni_estado(self):
        self.assertEqual(modes.automation_interval_label(), "según la frecuencia configurada")
        self.assertIsNone(modes.codex_automation_status())
        self.assertIn("No encontré", modes.set_codex_automation(True))

    def test_frecuencia_en_minutos_horas_o_desconocida(self):
        casos = {'rrule = "RRULE:FREQ=MINUTELY;INTERVAL=5"': "cada 5 minutos",
                 'rrule = "FREQ=MINUTELY"': "cada minuto", 'rrule = "FREQ=HOURLY;INTERVAL=2"': "cada 2 horas",
                 'rrule = "FREQ=HOURLY"': "cada hora", 'rrule = "FREQ=DAILY"': "según la frecuencia configurada",
                 "nada": "según la frecuencia configurada"}
        for texto, esperado in casos.items():
            self.escribir(texto)
            self.assertEqual(modes.automation_interval_label(), esperado, texto)

    def test_pausar_y_reactivar_cambia_solo_la_linea_status(self):
        self.escribir('name = "x"\nstatus = "ACTIVE"\n')
        self.assertEqual(modes.codex_automation_status(), "ACTIVE")
        self.assertIn("ya estaba activa", modes.set_codex_automation(True))
        self.assertIn("pausada", modes.set_codex_automation(False))
        self.assertEqual(self.archivo.read_text(encoding="utf-8"), 'name = "x"\nstatus = "PAUSED"\n')
        self.escribir('name = "x"\n')
        self.assertIsNone(modes.codex_automation_status())
        self.assertIn("no tiene campo status", modes.set_codex_automation(True))

    def test_set_mode_en_cada_modo(self):
        guardado = {}
        config = {"active_mode": "local", "llm": {"provider": "ollama"}}
        with patch.object(modes, "load_config", return_value=config), \
                patch.object(modes, "write_config", side_effect=guardado.update), \
                patch.object(modes, "check_ollama") as ollama:
            with self.assertRaises(ErrorDeAnka):
                modes.set_mode("otro")
            self.assertIn("ChatGPT Work activado", modes.set_mode("chatgpt_work"))
            self.assertEqual(guardado["active_mode"], "chatgpt_work")
            self.assertIn("Modo local activado", modes.set_mode("local"))
            ollama.assert_called_once()
            with patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
                self.assertIn("Modo API activado", modes.set_mode("api"))
            self.assertEqual(guardado["llm"]["provider"], "openai")
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ErrorDeAnka, "sin clave|clave"):
                modes.set_mode("api")


# Cubre: T-06, T-27
class LanzadorTests(unittest.TestCase):
    def test_un_archivo_que_desaparece_no_detiene_la_deteccion_de_cambios(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "anka").mkdir()
            (root / "anka" / "a.py").write_text("1")
            real = Path.stat

            def stat(ruta, *args, **kwargs):
                if ruta.suffix == ".py":
                    raise FileNotFoundError     # se borró entre el recorrido y la lectura de su fecha
                return real(ruta, *args, **kwargs)

            with patch.object(Path, "stat", autospec=True, side_effect=stat), \
                    self.assertLogs("anka.cli.supervisor", "DEBUG") as registro:
                self.assertEqual(supervisor.code_snapshot(root), {})
        self.assertIn("FileNotFoundError", registro.output[0])

    def test_lista_de_procesos_de_windows(self):
        fila = {"ProcessId": 9, "ParentProcessId": 0, "CommandLine": "python app.py review-ui"}
        with patch.object(supervisor.subprocess, "run", return_value=MagicMock(stdout=json.dumps(fila))):
            self.assertEqual(supervisor.old_instances(own_pid=1), [9])           # un solo proceso llega como objeto
        with patch.object(supervisor.subprocess, "run", side_effect=OSError):
            self.assertEqual(supervisor.old_instances(own_pid=1), [])            # sin PowerShell no cierra nada
        with patch.object(supervisor.subprocess, "run", return_value=MagicMock(stdout="no es json")):
            self.assertEqual(supervisor.old_instances(own_pid=1), [])

    def test_cierra_las_instancias_viejas_y_espera_que_liberen_el_puerto(self):
        with patch.object(supervisor, "old_instances", return_value=[7, 8]), \
                patch.object(supervisor.subprocess, "run") as run, patch.object(supervisor.time, "sleep") as sleep:
            self.assertEqual(supervisor.stop_old_instances(), 2)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()
        with patch.object(supervisor, "old_instances", return_value=[]), \
                patch.object(supervisor.time, "sleep") as sleep:
            self.assertEqual(supervisor.stop_old_instances(), 0)
        sleep.assert_not_called()

    def test_modo_work_y_preparador_desde_la_configuracion(self):
        casos = [({"active_mode": "chatgpt_work", "work_drive_enabled": True}, (True, True)),
                 ({"active_mode": "chatgpt_work"}, (True, False)),
                 ({"active_mode": "local", "work_drive_enabled": True}, (False, False))]
        for config, esperado in casos:
            with patch.object(settings, "load_config", return_value=config):
                self.assertEqual((supervisor.work_mode(), supervisor.work_drive_enabled()), esperado, config)
        with patch.object(settings, "load_config", side_effect=ErrorDeAnka("config dañada")):
            self.assertEqual((supervisor.work_mode(), supervisor.work_drive_enabled()), (False, False))

    def test_un_hijo_estable_olvida_sus_fallos_y_al_detenerlo_se_cierra_su_arbol(self):
        child = supervisor.Child("X", ["x.py"])
        with patch.object(supervisor.subprocess, "Popen") as popen, \
                patch.object(supervisor.subprocess, "run") as run, patch.object(supervisor.time, "time") as reloj:
            popen.return_value.poll.return_value = None
            popen.return_value.pid = 5
            reloj.return_value = 1000.0
            child.check()                                   # arranca
            child.failures = 3
            reloj.return_value = 1000.0 + 121
            child.check()                                   # lleva más de 2 min vivo
            self.assertEqual(child.failures, 0)
            child.stop()
        self.assertIn("taskkill", run.call_args[0][0])
        self.assertIsNone(child.process)

    def test_el_servicio_sigue_si_fallan_los_recordatorios_y_para_el_puente_al_salir_de_work(self):
        hijos = {}

        class Hijo:
            def __init__(self, nombre, args):
                self.name, self.process, self.stopped, self.stop_calls = nombre, None, False, 0
                hijos[nombre] = self

            def check(self):
                self.process = object()

            def stop(self):
                self.stop_calls += 1
                self.process = None

        with patch.object(supervisor, "configure_logging"), patch.object(supervisor, "stop_old_instances"), \
                patch.object(supervisor, "Child", Hijo), patch.object(supervisor, "MODE_RECHECK", 0), \
                patch.object(supervisor, "code_snapshot", return_value={"a": 1.0}), \
                patch.object(supervisor, "work_mode", side_effect=[True, False]), \
                patch.object(supervisor, "work_drive_enabled", side_effect=[True, False]), \
                patch.object(supervisor, "CopiaDeSeguridad") as copia, patch.object(supervisor, "Reconciliador"), \
                patch.object(supervisor, "run_due_reminders", side_effect=RuntimeError("sin red")), \
                patch.object(supervisor.time, "sleep", side_effect=[None, KeyboardInterrupt]), \
                self.assertLogs("anka.cli.supervisor", "INFO") as registro:
            self.assertEqual(supervisor.servicio(), 0)                 # Ctrl+C: parada normal
        self.assertEqual(copia.return_value.check.call_count, 2)       # la copia se vigila en cada vuelta, sin esperarla
        copia.return_value.stop.assert_called_once()                   # y se cancela al salir
        self.assertTrue(any("recordatorios" in linea for linea in registro.output))
        puente, preparador = hijos["Puente Work"], hijos["Expedientes Work"]
        self.assertEqual((puente.stop_calls, preparador.stop_calls), (2, 2))  # al cambiar de modo y al salir
        self.assertFalse(puente.stopped)

    def test_el_guardian_descansa_si_el_servicio_pide_relanzarse_muy_seguido(self):
        with patch.object(supervisor.subprocess, "call", side_effect=[supervisor.EXIT_RELANZAR, 0]), \
                patch.object(supervisor.time, "sleep") as sleep, patch.object(supervisor, "configure_logging"):
            self.assertEqual(supervisor.guardian(), 0)
        sleep.assert_called_once_with(2)


# Cubre: T-06
class LatidoTests(_DatosTemporales):
    def test_un_disco_que_falla_no_tumba_al_puente(self):
        with patch.object(Path, "write_text", side_effect=PermissionError), \
                self.assertLogs("anka.infra.work_heartbeat", "WARNING") as registro:
            work_heartbeat.write_heartbeat(30)
        self.assertIn("PermissionError", registro.output[0])
        self.assertIsNone(work_heartbeat.read_heartbeat())

    def test_la_peticion_de_revisar_ya_se_atiende_aunque_no_se_pueda_borrar(self):
        work_heartbeat.request_check_now()
        with patch.object(Path, "unlink", side_effect=PermissionError), \
                self.assertLogs("anka.infra.work_heartbeat", "DEBUG"):
            self.assertTrue(work_heartbeat.wait_for_next_cycle(5, sleep=lambda _: None))
        (self.data / work_heartbeat.WAKE_FILE).unlink()
        self.assertFalse(work_heartbeat.wait_for_next_cycle(2, sleep=lambda _: None))

    def test_procesos_vivos_y_latido_propio(self):
        self.assertTrue(work_heartbeat.pid_alive(os.getpid()))
        self.assertFalse(work_heartbeat.pid_alive(0))
        self.assertFalse(work_heartbeat.pid_alive(2 ** 22 + 3))        # un PID que no existe
        work_heartbeat.write_heartbeat(30)
        self.assertFalse(work_heartbeat.other_bridge_running())        # el latido es de este mismo proceso

    def test_el_puente_detenido_hace_horas_se_cuenta_en_horas(self):
        with patch.object(work_heartbeat, "_now", return_value=AHORA):
            datos = {"at": (AHORA - timedelta(hours=3)).isoformat(timespec="seconds"), "pid": 1, "interval": 30}
            (self.data / work_heartbeat.HEARTBEAT_FILE).write_text(json.dumps(datos))
            estado, texto = work_heartbeat.bridge_state()
        self.assertEqual(estado, "stopped")
        self.assertIn("hace 3 h", texto)


# Cubre: T-25
class CacheDeHallazgosTests(_DatosTemporales):
    def test_un_fallo_de_disco_al_guardar_no_impide_el_analisis(self):
        with patch.object(Path, "write_text", side_effect=OSError("disco lleno")), \
                self.assertLogs("anka.infra.cache_hallazgos", "WARNING") as registro:
            cache_hallazgos.guardar("k", [])
        self.assertIn("OSError", registro.output[0])
        self.assertIsNone(cache_hallazgos.leer("k"))


# Cubre: T-06
class CopiasTests(_DatosTemporales):
    def crear_base(self):
        db = sqlite3.connect(self.data / "quotes.sqlite3")
        db.execute("CREATE TABLE t (x)")
        db.commit()
        db.close()

    def test_sin_base_la_copia_falla_con_constancia(self):
        with self.assertLogs("anka.infra.backup", "ERROR"):
            result = backup.run_backup(use_drive=False)
        self.assertIn("FileNotFoundError", result["error"])
        self.assertEqual(result["file"], "")
        self.assertTrue(backup.backup_due())                           # sin copia exitosa sigue pendiente

    def test_si_no_se_puede_anotar_la_copia_se_avisa_y_se_repite(self):
        self.crear_base()
        real = Path.write_text

        def escribir(ruta, *args, **kwargs):
            if ruta.name == backup.LAST_FILE:
                raise PermissionError
            return real(ruta, *args, **kwargs)

        with patch.object(Path, "write_text", escribir), \
                self.assertLogs("anka.infra.backup", "WARNING") as registro:
            result = backup.run_backup(use_drive=False)
        self.assertTrue(result["file"])
        self.assertTrue(any("PermissionError" in linea for linea in registro.output))
        self.assertTrue(backup.backup_due())

    def test_sin_drive_explicito_se_conecta_y_crea_la_carpeta(self):
        self.crear_base()
        drive = MagicMock()
        files = drive.files.return_value
        files.list.return_value.execute.side_effect = [{"files": []}, {"files": []}]
        files.create.return_value.execute.return_value = {"id": "F"}
        with patch("anka.infra.google_services.services", return_value=(None, drive)):
            result = backup.run_backup(use_drive=True)
        self.assertTrue(result["drive"])
        carpeta = files.create.call_args_list[0].kwargs["body"]
        self.assertEqual(carpeta["name"], backup.DRIVE_FOLDER)

    def test_una_anotacion_sin_archivo_cuenta_como_pendiente(self):
        (self.data / backup.LAST_FILE).write_text(json.dumps({"at": AHORA.isoformat(), "file": ""}))
        self.assertTrue(backup.backup_due(AHORA))

    def zip(self, entradas):
        ruta = self.data / "r.zip"
        with zipfile.ZipFile(ruta, "w") as archivo:
            for nombre, contenido in entradas.items():
                archivo.writestr(nombre, contenido)
        return ruta

    def test_restaurar_rechaza_respaldos_incompletos_o_con_rutas_peligrosas(self):
        for entradas in ({"config.json": "{}"}, {"quotes.sqlite3": "x", "../fuera.txt": "x"},
                         {"quotes.sqlite3": "x", "token.json": "SECRETO"}):
            with self.assertRaises(ValueError, msg=str(entradas)):
                backup.restore(self.zip(entradas))

    def test_restaurar_rechaza_una_base_danada(self):
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backup.restore(self.zip({"quotes.sqlite3": "no es una base"}))

    def test_si_la_instalacion_falla_se_revierte_lo_ya_copiado(self):
        self.crear_base()
        (self.data / "config.json").write_text('{"antes": 1}')
        copia = backup.create_backup()
        (self.data / "config.json").write_text('{"actual": 1}')
        real = backup.shutil.copy2

        def copiar(origen, destino):
            if Path(destino).name.startswith("quotes.sqlite3") and "before-restore" not in Path(destino).name \
                    and Path(origen).parent != self.data:
                raise OSError("bloqueado")
            return real(origen, destino)

        with patch.object(backup.shutil, "copy2", side_effect=copiar), self.assertRaises(OSError):
            backup.restore(copia)
        self.assertEqual(json.loads((self.data / "config.json").read_text()), {"actual": 1})

    def test_el_comando_crea_o_restaura(self):
        self.crear_base()
        out = io.StringIO()
        with patch.object(sys, "argv", ["backup.py", "--no-drive"]), patch.object(backup, "configure_logging"), \
                redirect_stdout(out), self.assertRaises(SystemExit) as salida:
            backup.main()
        self.assertEqual(salida.exception.code, 0)
        archivo = json.loads(out.getvalue())["file"]
        with patch.object(sys, "argv", ["backup.py", "--restore", str(backup.backups_dir() / archivo)]), \
                patch.object(backup, "dashboard_en_marcha", return_value=False), \
                patch.object(backup, "configure_logging"), redirect_stdout(io.StringIO()) as salida_restaurar:
            backup.main()
        self.assertIn("Restaurada:", salida_restaurar.getvalue())


# Cubre: T-06
class LineaDeComandosTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with patch.object(sys, "argv", ["app.py", *argv]), patch.object(app, "configure_logging"), \
                patch.object(app, "warn_unknown_config_keys"), redirect_stdout(out), redirect_stderr(io.StringIO()):
            app.main()
        return out.getvalue()

    def test_cada_comando_llega_a_su_servicio(self):
        casos = [
            (("dashboard-upsert", "--payload-base64", "e30="), "dashboard_upsert", "Registro del dashboard"),
            (("prepare-work-packet", "--attachment", "a.pdf", "--output-dir", "x"),
             "prepare_work_attachment_packet", "{"),
            (("auth",), "authorize_interactive", "Autorización lista."),
            (("set-mode", "--active-mode", "local"), "set_mode", ""),
            (("reviews",), "list_reviews", ""),
            (("approve", "--thread-id", "T1"), "approve_review", ""),
            (("review-ui", "--no-browser"), "review_ui", ""),
            (("run",), "run", ""),
            (("run", "--watch-seconds", "60"), "watch", ""),
        ]
        for argv, servicio, texto in casos:
            with patch.object(app, servicio, return_value={"ok": True} if servicio.startswith("prepare") else "")\
                    as llamado:
                self.assertIn(texto, self.run_cli(*argv), argv)
            llamado.assert_called_once()

    def test_faltan_opciones_obligatorias(self):
        for argv in (("dashboard-upsert",), ("prepare-work-packet",), ("prepare-work-packet", "--attachment", "a"),
                     ("analyze-text",), ("set-mode",), ("approve",)):
            with self.assertRaises(SystemExit) as salida:
                self.run_cli(*argv)
            self.assertEqual(salida.exception.code, 2, argv)

    def test_analizar_un_texto_desde_archivo(self):
        config = {"required_fields": ["ubicacion"], "llm": {"provider": "ollama", "model": "m"}}
        with tempfile.TemporaryDirectory() as temp:
            ruta = Path(temp) / "correo.txt"
            ruta.write_text("Necesito un levantamiento", encoding="utf-8")
            with patch.object(app, "load_config", return_value=config), \
                    patch.object(app, "analyze_text", return_value={"summary": "s"}) as analizar, \
                    patch.object(app, "reception_questions", return_value=[]):
                salida = json.loads(self.run_cli("analyze-text", "--file", str(ruta)))
        self.assertEqual(analizar.call_args[0][0], "Necesito un levantamiento")
        self.assertEqual(salida["provider"], "ollama")

    def test_arbol_de_drive(self):
        carpetas = [[{"name": "2026", "id": "A"}], [{"name": "Cliente", "id": "B"}]]
        with patch.object(app, "services", return_value=(None, MagicMock())), \
                patch.object(app, "list_folders", side_effect=carpetas):
            self.assertEqual(self.run_cli("drive-tree"), "2026 [A]\n  Cliente [B]\n")

    def test_el_preparador_con_fallos_sale_con_error(self):
        with patch("anka.cli.work_drive.run", return_value={"failures": 1}):
            with self.assertRaises(SystemExit) as salida:
                self.run_cli("work-drive-sync")
        self.assertIn("Preparación incompleta", str(salida.exception.code))
        with patch("anka.cli.work_drive.run", return_value={"status": "desactivado"}):
            self.assertIn("desactivado", self.run_cli("work-drive-sync"))

    def test_aviso_de_claves_desconocidas(self):
        err = io.StringIO()
        with patch.object(app, "load_config", return_value={"activ_mode": "x"}), \
                patch.object(app, "unknown_config_keys", return_value=["activ_mode"]), redirect_stderr(err):
            app.warn_unknown_config_keys()
        self.assertIn("activ_mode", err.getvalue())
        with patch.object(app, "load_config", side_effect=ErrorDeAnka("sin config")):
            app.warn_unknown_config_keys()                       # el comando informará por su cuenta


# Cubre: T-04
class SeguimientoDeGmailTests(unittest.TestCase):
    def test_una_parte_con_codificacion_desconocida_se_omite_sin_registrar_su_contenido(self):
        from anka.servicios import gmail_followup
        crudo = (b"Subject: Plano\r\nMIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary=B\r\n\r\n"
                 b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nlegible\r\n"
                 b"--B\r\nContent-Type: text/plain; charset=x-desconocido\r\n\r\nsecreto del cliente\r\n--B--\r\n")
        gmail = MagicMock()
        gmail.users.return_value.messages.return_value.get.return_value.execute.return_value = {
            "raw": base64.urlsafe_b64encode(crudo).decode()}
        with self.assertLogs("anka.servicios.gmail_followup", "DEBUG") as registro:
            datos, texto = gmail_followup._read_raw(gmail, "M1")
        self.assertEqual(datos, crudo)
        self.assertIn("legible", texto)
        self.assertNotIn("secreto", texto)
        self.assertIn("LookupError", registro.output[0])
        self.assertNotIn("secreto", registro.output[0])


def _b64(texto):
    return base64.urlsafe_b64encode(texto.encode()).decode().rstrip("=")


# Cubre: T-02
class LecturaDelBorradorTests(unittest.TestCase):
    def test_texto_de_partes_anidadas_y_html(self):
        mensaje = {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plano")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>uno</p><br>dos &amp; tres")}},
            {"mimeType": "image/png", "body": {"data": _b64("x")}}]}
        self.assertEqual(sync.body_text(mensaje), ["plano", "uno\n\ndos & tres"])

    def borrador(self, texto):
        return {"threadId": "T1", "payload": {"mimeType": "text/plain", "body": {"data": _b64(texto)}}}

    def test_bloques_mal_puestos_se_rechazan(self):
        cabecera = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\n"
        casos = {
            cabecera + "sin bloque": sync.SIN_BLOQUE,
            "texto antes\n" + sync.START + "{}" + sync.END: "sección interna inicial",
            cabecera + sync.START + "{}" + sync.END + sync.START: "repetidos",
            cabecera + sync.START + "[]" + sync.END: "objeto",
        }
        for texto, motivo in casos.items():
            with self.assertRaisesRegex(ValueError, motivo):
                sync.extract(self.borrador(texto))
        self.assertIsNone(sync.extract(self.borrador("un correo cualquiera")))

    def test_hora_del_borrador_inutilizable(self):
        self.assertEqual(sync._hora_del_borrador(0), "")
        self.assertEqual(sync._hora_del_borrador(10 ** 20), "")


# Cubre: T-06, T-28
class BucleDelPuenteTests(_DatosTemporales):
    CUENTA = "puente@example.com"

    def ejecutar(self, *argv, modos=None, sincronizar=None, seguimiento=None, cuenta=None):
        modos = modos or [{"active_mode": "chatgpt_work", "work_sync_account": self.CUENTA}] * 4
        gmail = MagicMock()
        gmail.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": cuenta or self.CUENTA}
        self.latidos = []
        with patch.object(sys, "argv", ["work_dashboard_sync.py", *argv]), \
                patch.object(sync, "configure_logging"), patch.object(sync, "load_config", side_effect=modos), \
                patch.object(sync, "services", return_value=(gmail, None)) as servicios, \
                patch.object(sync, "other_bridge_running", return_value=False), \
                patch.object(sync, "sync_once", side_effect=sincronizar or (lambda *a: {
                    "drafts": 1, "updated": 0, "failures": 0, "rejected": []})), \
                patch.object(sync, "sync_thread_activity", side_effect=seguimiento or (lambda *a: {
                    "events": 1, "moved": 1, "related": 0})), \
                patch.object(sync, "write_heartbeat", side_effect=lambda *a, **k: self.latidos.append(k)), \
                patch.object(sync, "wait_for_next_cycle"), redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as salida:
            sync.main()
        self.servicios = servicios
        return salida.exception.code

    def test_una_pasada_correcta_sale_con_cero_y_deja_latido(self):
        self.assertEqual(self.ejecutar("--once"), 0)
        self.assertEqual(self.latidos[0]["events"], 1)
        self.assertEqual(self.latidos[0]["error"], "")

    def test_fuera_del_modo_work_o_con_otra_cuenta_no_se_reinicia(self):
        self.assertEqual(self.ejecutar("--once", modos=[{"active_mode": "local"}]), sync.EXIT_STOP)
        self.assertEqual(self.ejecutar("--once", cuenta="otra@example.com"), sync.EXIT_STOP)

    def test_otro_puente_vivo_impide_un_segundo(self):
        with patch.object(sync, "other_bridge_running", return_value=True), \
                patch.object(sync, "load_config", return_value={"active_mode": "chatgpt_work"}), \
                patch.object(sync, "configure_logging"), patch.object(sys, "argv", ["x"]), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as salida:
            sync.main()
        self.assertEqual(salida.exception.code, sync.EXIT_STOP)

    def test_un_fallo_del_seguimiento_no_frena_la_importacion(self):
        codigo = self.ejecutar("--once", seguimiento=RuntimeError("cuota"))
        self.assertEqual(codigo, 1)
        self.assertIn("Seguimiento: RuntimeError", self.latidos[0]["error"])

    def test_un_fallo_de_gmail_reconecta_en_el_ciclo_siguiente_y_el_cambio_de_modo_lo_detiene(self):
        trabajo = {"active_mode": "chatgpt_work", "work_sync_account": self.CUENTA}
        pasadas = [ConnectionError("sin red"), {"drafts": 0, "updated": 0, "failures": 0, "rejected": []}]
        codigo = self.ejecutar("--seconds", "30", modos=[trabajo, trabajo, trabajo, trabajo, {"active_mode": "api"}],
                               sincronizar=pasadas)
        self.assertEqual(codigo, sync.EXIT_STOP)
        self.assertEqual(self.servicios.call_count, 2)                 # volvió a conectarse tras el fallo
        self.assertIn("ConnectionError", self.latidos[0]["error"])
        self.assertEqual(self.latidos[1]["error"], "")

    def test_ciclo_demasiado_corto_se_rechaza(self):
        with patch.object(sys, "argv", ["x", "--seconds", "5"]), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as salida:
            sync.main()
        self.assertEqual(salida.exception.code, 2)

    def test_cli_convierte_ctrl_c_y_errores_de_anka(self):
        with patch.object(sync, "main", side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as salida:
            sync.cli()
        self.assertEqual(salida.exception.code, sync.EXIT_STOP)
        with patch.object(sync, "main", side_effect=ErrorDeAnka("token vencido")), \
                self.assertRaises(SystemExit) as salida:
            sync.cli()
        self.assertEqual(salida.exception.code, "token vencido")


if __name__ == "__main__":
    unittest.main()
